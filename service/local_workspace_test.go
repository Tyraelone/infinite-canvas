package service

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/libtnb/sqlite"
	"github.com/tigerowo/infinite-canvas/model"
	"github.com/tigerowo/infinite-canvas/repository"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func testLocalWorkspace(t *testing.T) (*LocalWorkspace, *gorm.DB, string) {
	t.Helper()
	root := t.TempDir()
	db, err := gorm.Open(sqlite.Open(filepath.Join(root, "workspace.db")+"?_txlock=immediate"), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	if err != nil {
		t.Fatal(err)
	}
	sqlDB, _ := db.DB()
	sqlDB.SetMaxOpenConns(1)
	t.Cleanup(func() { _ = sqlDB.Close() })
	if err := db.AutoMigrate(&model.LocalWorkspaceState{}, &model.LocalWorkspaceFile{}, &model.LocalWorkspaceMigration{}, &model.LocalWorkspaceTombstone{}); err != nil {
		t.Fatal(err)
	}
	return NewLocalWorkspace(repository.NewLocalWorkspace(db), filepath.Join(root, "workspace")), db, root
}

func stringPointer(value string) *string { return &value }

func importRequest(id, canvas, assets string, files ...model.LocalWorkspaceImportFile) model.LocalWorkspaceImportRequest {
	r := model.LocalWorkspaceImportRequest{MigrationID: id, States: map[string]*string{}, Files: files}
	if canvas != "" {
		r.States["canvas"] = stringPointer(canvas)
	}
	if assets != "" {
		r.States["assets"] = stringPointer(assets)
	}
	return r
}

func TestLocalWorkspaceDurabilityAndCAS(t *testing.T) {
	w, _, root := testLocalWorkspace(t)
	initial, err := w.State("canvas")
	if err != nil || initial.Value != nil || initial.Revision != 0 {
		t.Fatalf("initial state: %+v %v", initial, err)
	}
	value := `{"state":{"projects":[{"id":"p1","nodes":[],"title":"first"}]},"version":0}`
	revision, err := w.PutState("canvas", value, 0)
	if err != nil || revision != 1 {
		t.Fatalf("save: %d %v", revision, err)
	}
	if _, err := w.PutState("canvas", `{"state":{"projects":[]}}`, 0); !errors.Is(err, ErrLocalWorkspaceConflict) {
		t.Fatalf("stale write accepted: %v", err)
	}
	db, err := gorm.Open(sqlite.Open(filepath.Join(root, "workspace.db")), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	if err != nil {
		t.Fatal(err)
	}
	sqlDB, _ := db.DB()
	defer sqlDB.Close()
	reopened := NewLocalWorkspace(repository.NewLocalWorkspace(db), filepath.Join(root, "workspace"))
	saved, err := reopened.State("canvas")
	if err != nil || saved.Value == nil || *saved.Value != value || saved.Revision != 1 {
		t.Fatalf("durable state: %+v %v", saved, err)
	}
	if _, err := w.PutState("canvas", `{"state":{"projects":[{"title":"missing id"}]}}`, 1); err == nil {
		t.Fatal("invalid entity accepted")
	}
}

func TestLocalWorkspaceMediaImmutableAndDeduplicated(t *testing.T) {
	w, db, _ := testLocalWorkspace(t)
	first, err := w.Upload("image:one", "image/png", strings.NewReader("abc"), "")
	if err != nil || first.SHA256 != "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" || first.Bytes != 3 {
		t.Fatalf("upload: %+v %v", first, err)
	}
	second, err := w.Upload("image:two", "image/png", strings.NewReader("abc"), first.SHA256)
	if err != nil {
		t.Fatal(err)
	}
	if first.URL != "/api/local-runtime/workspace/files/image:one" || second.SHA256 != first.SHA256 {
		t.Fatalf("media mapping: %+v %+v", first, second)
	}
	if _, err := w.Upload("image:one", "image/png", strings.NewReader("different"), ""); !errors.Is(err, ErrLocalWorkspaceConflict) {
		t.Fatalf("overwrite accepted: %v", err)
	}
	if _, err := w.Upload("image:bad", "image/png", strings.NewReader("abc"), strings.Repeat("0", 64)); err == nil {
		t.Fatal("wrong client hash accepted")
	}
	var records []model.LocalWorkspaceFile
	if err := db.Find(&records).Error; err != nil || len(records) != 2 {
		t.Fatalf("metadata: %+v %v", records, err)
	}
	file, path, err := w.File("image:two")
	content, readErr := os.ReadFile(path)
	if err != nil || readErr != nil || file.Bytes != 3 || string(content) != "abc" {
		t.Fatalf("dedup read: %q %v %v", content, err, readErr)
	}
	var diskFiles int
	_ = filepath.WalkDir(filepath.Join(w.Root, "files"), func(_ string, entry os.DirEntry, err error) error {
		if err == nil && !entry.IsDir() {
			diskFiles++
		}
		return err
	})
	if diskFiles != 1 {
		t.Fatalf("expected one content object, got %d", diskFiles)
	}
}

func TestLocalWorkspaceImportMergesIdempotentlyAndPreservesManifest(t *testing.T) {
	w, _, _ := testLocalWorkspace(t)
	_, err := w.PutState("canvas", `{"state":{"projects":[{"id":"existing","title":"server"}]},"version":0}`, 0)
	if err != nil {
		t.Fatal(err)
	}
	upload, err := w.Upload("video:one", "video/mp4", strings.NewReader("abc"), "")
	if err != nil {
		t.Fatal(err)
	}
	r := importRequest("browser-1", `{"state":{"projects":[{"id":"existing","title":"browser"},{"id":"new","nodes":[{"metadata":{"storageKey":"video:one"}}]}]},"version":0}`, `{"state":{"assets":[{"id":"a1"}]}}`, model.LocalWorkspaceImportFile{Key: upload.Key, SHA256: upload.SHA256})
	raw, _ := json.MarshalIndent(r, "", "  ")
	report, err := w.Import(r, raw)
	if err != nil || report.ProjectCount != 2 || report.AssetCount != 1 || report.FileCount != 1 || report.Bytes != 3 || report.Revisions["canvas"] != 2 || report.Revisions["assets"] != 1 {
		t.Fatalf("import report: %+v %v", report, err)
	}
	saved, _ := w.State("canvas")
	if strings.Contains(*saved.Value, "browser") || !strings.Contains(*saved.Value, "server") || !strings.Contains(*saved.Value, `"new"`) {
		t.Fatalf("server ID overwritten: %s", *saved.Value)
	}
	_, err = w.PutState("assets", `{"state":{"assets":[]}}`, 1)
	if err != nil {
		t.Fatal(err)
	}
	again, err := w.Import(r, raw)
	if err != nil || again.Revisions["assets"] != 1 {
		t.Fatalf("repeat must return original report: %+v %v", again, err)
	}
	assetState, _ := w.State("assets")
	if assetState.Revision != 2 || strings.Contains(*assetState.Value, "a1") {
		t.Fatalf("repeat published again: %+v", assetState)
	}
	var found bool
	_ = filepath.WalkDir(filepath.Join(w.Root, "migrations"), func(path string, entry os.DirEntry, err error) error {
		if err == nil && entry.Name() == "manifest.json" {
			content, _ := os.ReadFile(path)
			found = bytes.Equal(content, raw)
		}
		return err
	})
	if !found {
		t.Fatal("exact original migration manifest missing")
	}
	status, err := w.Status()
	if err != nil || status.ProjectCount != 2 || status.AssetCount != 0 || status.FileCount != 1 || status.Bytes != 3 {
		t.Fatalf("status: %+v %v", status, err)
	}
}

func TestLocalWorkspaceImportValidatesHashesReferencesAndDiskBytes(t *testing.T) {
	w, _, _ := testLocalWorkspace(t)
	upload, err := w.Upload("image:one", "image/png", strings.NewReader("abc"), "")
	if err != nil {
		t.Fatal(err)
	}
	for _, fixture := range []struct{ name, state, key, hash string }{
		{"wrong-hash", `{"state":{"projects":[]}}`, "image:one", strings.Repeat("0", 64)},
		{"missing-reference", `{"state":{"projects":[{"id":"p","nodes":[{"metadata":{"storageKey":"image:missing"}}]}]}}`, "image:one", upload.SHA256},
		{"unbacked-blob", `{"state":{"projects":[{"id":"p","nodes":[{"metadata":{"content":"blob:http://localhost/lost"}}]}]}}`, "image:one", upload.SHA256},
	} {
		t.Run(fixture.name, func(t *testing.T) {
			r := importRequest(fixture.name, fixture.state, `{"state":{"assets":[{"id":"a"}]}}`, model.LocalWorkspaceImportFile{Key: fixture.key, SHA256: fixture.hash})
			if _, err := w.Import(r, nil); err == nil {
				t.Fatal("invalid import accepted")
			}
			state, _ := w.State("assets")
			if state.Value != nil || state.Revision != 0 {
				t.Fatalf("partial import: %+v", state)
			}
		})
	}
	_, path, _ := w.File("image:one")
	if err := os.WriteFile(path, []byte("tampered"), 0600); err != nil {
		t.Fatal(err)
	}
	r := importRequest("corrupt-disk", `{"state":{"projects":[{"id":"p"}]}}`, "", model.LocalWorkspaceImportFile{Key: upload.Key, SHA256: upload.SHA256})
	if _, err := w.Import(r, nil); err == nil {
		t.Fatal("corrupted disk media accepted")
	}
}

func TestLocalWorkspaceImportTransactionRollsBackEarlierState(t *testing.T) {
	w, db, _ := testLocalWorkspace(t)
	// Fail the second state inside the real SQLite transaction, after canvas was inserted.
	if err := db.Exec(`CREATE TRIGGER fail_assets BEFORE INSERT ON local_workspace_states WHEN NEW.name = 'assets' BEGIN SELECT RAISE(ABORT, 'injected failure'); END`).Error; err != nil {
		t.Fatal(err)
	}
	r := importRequest("rollback", `{"state":{"projects":[{"id":"p"}]}}`, `{"state":{"assets":[{"id":"a"}]}}`)
	if _, err := w.Import(r, nil); err == nil {
		t.Fatal("injected database failure ignored")
	}
	state, _ := w.State("canvas")
	if state.Value != nil || state.Revision != 0 {
		t.Fatalf("first state survived rollback: %+v", state)
	}
	var count int64
	db.Model(&model.LocalWorkspaceMigration{}).Count(&count)
	if count != 0 {
		t.Fatal("failed migration marked complete")
	}
	if err := db.Exec("DROP TRIGGER fail_assets").Error; err != nil {
		t.Fatal(err)
	}
	if _, err := w.Import(r, nil); err != nil {
		t.Fatalf("retry after rollback: %v", err)
	}
}

func TestLocalWorkspaceImportRollsBackWhenMigrationRecordCannotCommit(t *testing.T) {
	w, db, _ := testLocalWorkspace(t)
	if err := db.Exec(`CREATE TRIGGER fail_migration BEFORE INSERT ON local_workspace_migrations BEGIN SELECT RAISE(ABORT, 'migration record failure'); END`).Error; err != nil {
		t.Fatal(err)
	}
	r := importRequest("archive-rollback", `{"state":{"projects":[{"id":"p"}]}}`, `{"state":{"assets":[{"id":"a"}]}}`)
	if _, err := w.Import(r, nil); err == nil {
		t.Fatal("migration record failure ignored")
	}
	for _, name := range []string{"canvas", "assets"} {
		state, _ := w.State(name)
		if state.Value != nil || state.Revision != 0 {
			t.Fatalf("%s survived rollback: %+v", name, state)
		}
	}
	archives, err := os.ReadDir(filepath.Join(w.Root, "migrations"))
	if err != nil || len(archives) != 0 {
		t.Fatalf("failed report still archived: %+v %v", archives, err)
	}
}

func TestLocalWorkspaceTombstonesPreventOldBrowserResurrection(t *testing.T) {
	w, _, _ := testLocalWorkspace(t)
	original := `{"state":{"projects":[{"id":"removed"},{"id":"kept","nodes":[{"id":"node-removed"},{"id":"node-kept"}],"edges":[{"id":"edge-removed"}]}]}}`
	if _, err := w.PutState("canvas", original, 0); err != nil {
		t.Fatal(err)
	}
	if _, err := w.PutState("canvas", `{"state":{"projects":[{"id":"kept","nodes":[{"id":"node-kept"}],"edges":[]}]}}`, 1); err != nil {
		t.Fatal(err)
	}
	if _, err := w.PutState("assets", `{"state":{"assets":[{"id":"asset-removed"}]}}`, 0); err != nil {
		t.Fatal(err)
	}
	if _, err := w.PutState("assets", `{"state":{"assets":[]}}`, 1); err != nil {
		t.Fatal(err)
	}
	r := importRequest("old-browser", original, `{"state":{"assets":[{"id":"asset-removed"},{"id":"asset-new"}]}}`)
	report, err := w.Import(r, nil)
	if err != nil || report.ProjectCount != 1 || report.AssetCount != 1 {
		t.Fatalf("tombstones: %+v %v", report, err)
	}
	saved, _ := w.State("canvas")
	if strings.Contains(*saved.Value, "removed") {
		t.Fatalf("removed project or nested entity resurrected: %s", *saved.Value)
	}
	// A fresh revision represents an intentional edit, including canvas Undo.
	if _, err := w.PutState("canvas", original, saved.Revision); err != nil {
		t.Fatalf("fresh revision cannot intentionally restore removed IDs: %v", err)
	}
}
