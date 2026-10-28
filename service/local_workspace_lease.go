package service

import (
	"errors"
	"strings"
	"time"
)

const LocalWorkspaceLeaseSeconds = 45

var ErrLocalWorkspaceLocked = errors.New("另一页面正在制作，当前页面可查看；修改草稿已保留，请在制作完成后刷新")

type localWorkspaceWriterLease struct {
	client  string
	expires time.Time
}

// All access is under localWorkspaceWriteMu, the same lock as state transactions.
var localWorkspaceWriterLeases = map[string]localWorkspaceWriterLease{}

func (w *LocalWorkspace) AcquireWriterLease(client string, revision int64) error {
	if strings.TrimSpace(client) == "" || len(client) > 128 || revision < 0 {
		return invalidWorkspace("制作页面或版本无效")
	}
	localWorkspaceWriteMu.Lock()
	defer localWorkspaceWriteMu.Unlock()
	current := localWorkspaceWriterLeases[w.Root]
	if current.expires.After(time.Now()) {
		if current.client != client {
			return ErrLocalWorkspaceLocked
		}
	} else {
		state, err := w.Repo.State("canvas")
		if err != nil {
			return err
		}
		if state.Revision != revision {
			return ErrLocalWorkspaceConflict
		}
	}
	localWorkspaceWriterLeases[w.Root] = localWorkspaceWriterLease{client: client, expires: time.Now().Add(LocalWorkspaceLeaseSeconds * time.Second)}
	return nil
}

func (w *LocalWorkspace) checkWriterLease(clients []string) error {
	current := localWorkspaceWriterLeases[w.Root]
	if !current.expires.After(time.Now()) {
		delete(localWorkspaceWriterLeases, w.Root)
		return nil
	}
	if len(clients) != 1 || clients[0] != current.client {
		return ErrLocalWorkspaceLocked
	}
	return nil
}
