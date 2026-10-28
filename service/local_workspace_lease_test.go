package service

import (
	"errors"
	"testing"
	"time"
)

func TestLocalWorkspaceWriterLeaseFencesPassivePagesAndExpires(t *testing.T) {
	w, _, _ := testLocalWorkspace(t)
	value := `{"state":{"projects":[{"id":"film","nodes":[]}]},"version":0}`
	if _, err := w.PutState("canvas", value, 0); err != nil {
		t.Fatal(err)
	}
	if err := w.AcquireWriterLease("director", 0); !errors.Is(err, ErrLocalWorkspaceConflict) {
		t.Fatalf("stale acquire: %v", err)
	}
	if err := w.AcquireWriterLease("director", 1); err != nil {
		t.Fatal(err)
	}
	if err := w.AcquireWriterLease("viewer", 1); !errors.Is(err, ErrLocalWorkspaceLocked) {
		t.Fatalf("second writer: %v", err)
	}
	if _, err := w.PutState("canvas", value, 1, "viewer"); !errors.Is(err, ErrLocalWorkspaceLocked) {
		t.Fatalf("viewer write: %v", err)
	}
	state, err := w.State("canvas")
	if err != nil || state.Revision != 1 {
		t.Fatalf("view read: %+v %v", state, err)
	}
	if _, err := w.Import(importRequest("viewer-import", value, ""), []byte(`{}`), "viewer"); !errors.Is(err, ErrLocalWorkspaceLocked) {
		t.Fatalf("viewer import: %v", err)
	}
	if _, err := w.PutState("canvas", value, 1, "director"); err != nil {
		t.Fatal(err)
	}
	if err := w.AcquireWriterLease("director", 1); err != nil {
		t.Fatalf("owner renewal: %v", err)
	}
	localWorkspaceWriteMu.Lock()
	lease := localWorkspaceWriterLeases[w.Root]
	lease.expires = time.Now().Add(-time.Second)
	localWorkspaceWriterLeases[w.Root] = lease
	localWorkspaceWriteMu.Unlock()
	if err := w.AcquireWriterLease("viewer", 2); err != nil {
		t.Fatalf("expired takeover: %v", err)
	}
	if _, err := w.PutState("canvas", value, 2, "director"); !errors.Is(err, ErrLocalWorkspaceLocked) {
		t.Fatalf("old owner fence: %v", err)
	}
	if _, err := w.PutState("canvas", value, 2, "viewer"); err != nil {
		t.Fatal(err)
	}
}
