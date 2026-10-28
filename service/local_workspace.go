package service

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"github.com/google/uuid"
	"github.com/tigerowo/infinite-canvas/model"
	"github.com/tigerowo/infinite-canvas/repository"
	"gorm.io/gorm"
)

var (
	ErrLocalWorkspaceConflict = errors.New("共享工作区版本冲突，请刷新后重试")
	ErrLocalWorkspaceInvalid  = errors.New("共享工作区数据无效")
	ErrLocalWorkspaceNotFound = errors.New("共享工作区文件不存在")
	localWorkspaceWriteMu     sync.Mutex
)

type LocalWorkspace struct {
	Repo *repository.LocalWorkspaceRepository
	Root string
}

func NewLocalWorkspace(repo *repository.LocalWorkspaceRepository, root string) *LocalWorkspace {
	root, _ = filepath.Abs(root)
	return &LocalWorkspace{Repo: repo, Root: root}
}

func LocalWorkspaceAuthorized(authorization string) bool {
	token := os.Getenv("CANVAS_LOCAL_TOKEN")
	return os.Getenv("CANVAS_LOCAL_MODE") == "1" && token != "" && subtle.ConstantTimeCompare([]byte(authorization), []byte("Bearer "+token)) == 1
}

func invalidWorkspace(reason string) error {
	return fmt.Errorf("%w: %s", ErrLocalWorkspaceInvalid, reason)
}

func workspaceList(name string) (string, error) {
	switch name {
	case "canvas":
		return "projects", nil
	case "assets":
		return "assets", nil
	}
	return "", invalidWorkspace("状态名称只允许 canvas 或 assets")
}

type workspaceDocument struct {
	root  map[string]json.RawMessage
	state map[string]json.RawMessage
	items []json.RawMessage
	list  string
}

func parseWorkspaceState(name, value string) (*workspaceDocument, error) {
	list, err := workspaceList(name)
	if err != nil {
		return nil, err
	}
	doc := &workspaceDocument{list: list}
	if json.Unmarshal([]byte(value), &doc.root) != nil || doc.root == nil || json.Unmarshal(doc.root["state"], &doc.state) != nil || doc.state == nil {
		return nil, invalidWorkspace("缺少 Zustand state 对象")
	}
	if json.Unmarshal(doc.state[list], &doc.items) != nil || doc.items == nil {
		return nil, invalidWorkspace("缺少 " + list + " 数组")
	}
	seen := map[string]bool{}
	for _, item := range doc.items {
		id := workspaceEntityID(item)
		if id == "" || seen[id] {
			return nil, invalidWorkspace("实体 ID 为空或重复")
		}
		seen[id] = true
	}
	return doc, nil
}

func workspaceEntityID(raw json.RawMessage) string {
	var item struct {
		ID string `json:"id"`
	}
	if json.Unmarshal(raw, &item) != nil {
		return ""
	}
	return item.ID
}

func (d *workspaceDocument) value() (string, error) {
	items, err := json.Marshal(d.items)
	if err != nil {
		return "", err
	}
	d.state[d.list] = items
	state, err := json.Marshal(d.state)
	if err != nil {
		return "", err
	}
	d.root["state"] = state
	root, err := json.Marshal(d.root)
	return string(root), err
}

// Entity paths include their parent IDs, so unrelated nodes with the same ID do not collide.
func workspaceEntityPaths(value string) map[string]bool {
	paths := map[string]bool{}
	var root any
	_ = json.Unmarshal([]byte(value), &root)
	var walk func(any, string)
	walk = func(value any, path string) {
		switch item := value.(type) {
		case map[string]any:
			for key, child := range item {
				walk(child, path+"/"+url.PathEscape(key))
			}
		case []any:
			for index, child := range item {
				next := fmt.Sprintf("%s/%d", path, index)
				if object, ok := child.(map[string]any); ok {
					if id, ok := object["id"].(string); ok && id != "" {
						next = path + "/" + url.PathEscape(id)
						paths[next] = true
					}
				}
				walk(child, next)
			}
		}
	}
	walk(root, "")
	return paths
}

func (w *LocalWorkspace) State(name string) (model.LocalWorkspaceStateValue, error) {
	if _, err := workspaceList(name); err != nil {
		return model.LocalWorkspaceStateValue{}, err
	}
	return w.Repo.State(name)
}

func (w *LocalWorkspace) PutState(name, value string, revision int64, client ...string) (int64, error) {
	if revision < 0 {
		return 0, invalidWorkspace("版本无效")
	}
	if _, err := parseWorkspaceState(name, value); err != nil {
		return 0, err
	}
	localWorkspaceWriteMu.Lock()
	defer localWorkspaceWriteMu.Unlock()
	if err := w.checkWriterLease(client); err != nil {
		return 0, err
	}
	if _, err := w.validateMedia(nil, []string{value}, false); err != nil {
		return 0, err
	}
	err := w.Repo.Transaction(func(repo *repository.LocalWorkspaceRepository) error {
		current, err := repo.State(name)
		if err != nil {
			return err
		}
		if current.Revision != revision {
			return ErrLocalWorkspaceConflict
		}
		nextPaths := workspaceEntityPaths(value)
		var removed []string
		if current.Value != nil {
			for path := range workspaceEntityPaths(*current.Value) {
				if !nextPaths[path] {
					removed = append(removed, path)
				}
			}
		}
		if err := repo.AddTombstones(name, removed); err != nil {
			return err
		}
		changed, err := repo.CompareAndSwap(name, value, revision)
		if err == nil && !changed {
			return ErrLocalWorkspaceConflict
		}
		return err
	})
	return revision + 1, err
}

func validWorkspaceKey(key string) bool {
	return key != "" && len(key) <= 512 && !strings.ContainsAny(key, "\x00\r\n")
}

func validWorkspaceHash(hash string) bool {
	decoded, err := hex.DecodeString(hash)
	return err == nil && len(decoded) == sha256.Size && hash == strings.ToLower(hash)
}

func (w *LocalWorkspace) contentPath(hash string) string {
	return filepath.Join(w.Root, "files", hash[:2], hash)
}

func workspaceUpload(file model.LocalWorkspaceFile) model.LocalWorkspaceUpload {
	return model.LocalWorkspaceUpload{Key: file.Key, SHA256: file.SHA256, Bytes: file.Bytes, MimeType: file.MimeType, URL: "/api/local-runtime/workspace/files/" + url.PathEscape(file.Key)}
}

func (w *LocalWorkspace) Upload(key, contentType string, body io.Reader, expectedHash string) (model.LocalWorkspaceUpload, error) {
	if !validWorkspaceKey(key) {
		return model.LocalWorkspaceUpload{}, invalidWorkspace("文件 key 无效")
	}
	if expectedHash != "" && !validWorkspaceHash(expectedHash) {
		return model.LocalWorkspaceUpload{}, invalidWorkspace("SHA256 无效")
	}
	if contentType == "" {
		contentType = "application/octet-stream"
	}
	contentType, _, err := mime.ParseMediaType(contentType)
	if err != nil {
		return model.LocalWorkspaceUpload{}, invalidWorkspace("Content-Type 无效")
	}
	if err := os.MkdirAll(filepath.Join(w.Root, "files"), 0700); err != nil {
		return model.LocalWorkspaceUpload{}, err
	}
	temporary, err := os.CreateTemp(filepath.Join(w.Root, "files"), ".upload-")
	if err != nil {
		return model.LocalWorkspaceUpload{}, err
	}
	defer os.Remove(temporary.Name())
	hash := sha256.New()
	size, err := io.Copy(io.MultiWriter(temporary, hash), body)
	if err == nil {
		err = temporary.Sync()
	}
	closeErr := temporary.Close()
	if err != nil {
		return model.LocalWorkspaceUpload{}, err
	}
	if closeErr != nil {
		return model.LocalWorkspaceUpload{}, closeErr
	}
	digest := hex.EncodeToString(hash.Sum(nil))
	if expectedHash != "" && digest != expectedHash {
		return model.LocalWorkspaceUpload{}, invalidWorkspace("文件 SHA256 不匹配")
	}
	localWorkspaceWriteMu.Lock()
	defer localWorkspaceWriteMu.Unlock()
	current, err := w.Repo.File(key)
	if err == nil && current.SHA256 != digest {
		return model.LocalWorkspaceUpload{}, ErrLocalWorkspaceConflict
	}
	if err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
		return model.LocalWorkspaceUpload{}, err
	}
	path := w.contentPath(digest)
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return model.LocalWorkspaceUpload{}, err
	}
	if _, err := os.Stat(path); errors.Is(err, os.ErrNotExist) {
		if err := os.Rename(temporary.Name(), path); err != nil {
			return model.LocalWorkspaceUpload{}, err
		}
	} else if err != nil {
		return model.LocalWorkspaceUpload{}, err
	} else if err := verifyWorkspaceFile(path, digest, size); err != nil {
		return model.LocalWorkspaceUpload{}, err
	}
	file := model.LocalWorkspaceFile{Key: key, SHA256: digest, Bytes: size, MimeType: contentType}
	created, err := w.Repo.CreateFile(file)
	if err != nil {
		return model.LocalWorkspaceUpload{}, err
	}
	if !created {
		file, err = w.Repo.File(key)
		if err != nil {
			return model.LocalWorkspaceUpload{}, err
		}
		if file.SHA256 != digest {
			return model.LocalWorkspaceUpload{}, ErrLocalWorkspaceConflict
		}
	}
	return workspaceUpload(file), nil
}

func (w *LocalWorkspace) File(key string) (model.LocalWorkspaceFile, string, error) {
	if !validWorkspaceKey(key) {
		return model.LocalWorkspaceFile{}, "", invalidWorkspace("文件 key 无效")
	}
	file, err := w.Repo.File(key)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return file, "", ErrLocalWorkspaceNotFound
	}
	if err != nil {
		return file, "", err
	}
	if !validWorkspaceHash(file.SHA256) {
		return file, "", invalidWorkspace("文件哈希记录无效")
	}
	path := w.contentPath(file.SHA256)
	info, err := os.Stat(path)
	if errors.Is(err, os.ErrNotExist) {
		return file, "", ErrLocalWorkspaceNotFound
	}
	if err != nil {
		return file, "", err
	}
	if !info.Mode().IsRegular() || info.Size() != file.Bytes {
		return file, "", invalidWorkspace("磁盘文件长度不匹配")
	}
	return file, path, nil
}

func verifyWorkspaceFile(path, expectedHash string, expectedBytes int64) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	hash := sha256.New()
	size, err := io.Copy(hash, file)
	if err != nil {
		return err
	}
	if size != expectedBytes || hex.EncodeToString(hash.Sum(nil)) != expectedHash {
		return invalidWorkspace("磁盘文件 SHA256 不匹配")
	}
	return nil
}

func workspaceMediaReferences(value string) (map[string]bool, error) {
	keys := map[string]bool{}
	var root any
	if err := json.Unmarshal([]byte(value), &root); err != nil {
		return nil, invalidWorkspace("状态 JSON 无效")
	}
	var walk func(any) error
	walk = func(value any) error {
		switch item := value.(type) {
		case map[string]any:
			var hasKey bool
			for field, child := range item {
				if strings.HasSuffix(strings.ToLower(field), "storagekey") {
					if key, ok := child.(string); ok && key != "" {
						hasKey = true
						if !strings.HasPrefix(key, "server:") {
							keys[key] = true
						}
					}
				}
			}
			for field, child := range item {
				if text, ok := child.(string); ok {
					if strings.HasPrefix(text, "blob:") && !hasKey && (field == "url" || field == "content" || field == "dataUrl" || field == "coverUrl") {
						return invalidWorkspace("Blob 地址缺少可迁移的 storageKey")
					}
					const prefix = "/api/local-runtime/workspace/files/"
					if strings.HasPrefix(text, prefix) {
						key, err := url.PathUnescape(strings.TrimPrefix(text, prefix))
						if err != nil {
							return invalidWorkspace("文件地址无效")
						}
						keys[key] = true
					}
				}
				if err := walk(child); err != nil {
					return err
				}
			}
		case []any:
			for _, child := range item {
				if err := walk(child); err != nil {
					return err
				}
			}
		}
		return nil
	}
	return keys, walk(root)
}

// Imports hash the actual disk bytes before publishing either state; normal saves only stat immutable objects.
func (w *LocalWorkspace) validateMedia(listed []model.LocalWorkspaceImportFile, values []string, hashDisk bool) (map[string]model.LocalWorkspaceFile, error) {
	expected := map[string]string{}
	for _, file := range listed {
		if !validWorkspaceKey(file.Key) || !validWorkspaceHash(file.SHA256) {
			return nil, invalidWorkspace("迁移文件 key 或 SHA256 无效")
		}
		if previous, ok := expected[file.Key]; ok && previous != file.SHA256 {
			return nil, invalidWorkspace("迁移文件重复哈希冲突")
		}
		expected[file.Key] = file.SHA256
	}
	for _, value := range values {
		keys, err := workspaceMediaReferences(value)
		if err != nil {
			return nil, err
		}
		for key := range keys {
			if _, ok := expected[key]; !ok {
				expected[key] = ""
			}
		}
	}
	files := make(map[string]model.LocalWorkspaceFile, len(expected))
	verified := map[string]bool{}
	for key, hash := range expected {
		file, path, err := w.File(key)
		if err != nil {
			return nil, fmt.Errorf("%w: 缺少或损坏的媒体 %s (%v)", ErrLocalWorkspaceInvalid, key, err)
		}
		if hash != "" && hash != file.SHA256 {
			return nil, invalidWorkspace("迁移文件 SHA256 不匹配: " + key)
		}
		if hashDisk && !verified[file.SHA256] {
			if err := verifyWorkspaceFile(path, file.SHA256, file.Bytes); err != nil {
				return nil, err
			}
			verified[file.SHA256] = true
		}
		files[key] = file
	}
	return files, nil
}

func workspaceReport(repo *repository.LocalWorkspaceRepository) (model.LocalWorkspaceReport, error) {
	report := model.LocalWorkspaceReport{Revisions: map[string]int64{}}
	for _, name := range []string{"canvas", "assets"} {
		state, err := repo.State(name)
		if err != nil {
			return report, err
		}
		report.Revisions[name] = state.Revision
		if state.Value == nil {
			continue
		}
		doc, err := parseWorkspaceState(name, *state.Value)
		if err != nil {
			return report, err
		}
		if name == "canvas" {
			report.ProjectCount = len(doc.items)
		} else {
			report.AssetCount = len(doc.items)
		}
	}
	var err error
	report.FileCount, report.Bytes, err = repo.FileTotals()
	return report, err
}

func (w *LocalWorkspace) Status() (model.LocalWorkspaceReport, error) {
	var report model.LocalWorkspaceReport
	err := w.Repo.Transaction(func(repo *repository.LocalWorkspaceRepository) error {
		var err error
		report, err = workspaceReport(repo)
		return err
	})
	report.StorageRoot = w.Root
	return report, err
}

func (w *LocalWorkspace) Import(request model.LocalWorkspaceImportRequest, manifest []byte, client ...string) (model.LocalWorkspaceReport, error) {
	var report model.LocalWorkspaceReport
	if strings.TrimSpace(request.MigrationID) == "" || len(request.MigrationID) > 256 || request.States == nil {
		return report, invalidWorkspace("迁移 ID 或 states 无效")
	}
	localWorkspaceWriteMu.Lock()
	defer localWorkspaceWriteMu.Unlock()
	if err := w.checkWriterLease(client); err != nil {
		return report, err
	}
	existing, err := w.Repo.Migration(request.MigrationID)
	if err != nil {
		return report, err
	}
	if existing != nil {
		err := json.Unmarshal([]byte(existing.Report), &report)
		return report, err
	}
	incoming := map[string]*workspaceDocument{}
	var values []string
	for name, value := range request.States {
		if _, err := workspaceList(name); err != nil {
			return report, err
		}
		if value == nil {
			continue
		}
		doc, err := parseWorkspaceState(name, *value)
		if err != nil {
			return report, err
		}
		incoming[name] = doc
		values = append(values, *value)
	}
	files, err := w.validateMedia(request.Files, values, true)
	if err != nil {
		return report, err
	}
	if len(manifest) == 0 {
		manifest, err = json.Marshal(request)
		if err != nil {
			return report, err
		}
	}
	migrations := filepath.Join(w.Root, "migrations")
	if err := os.MkdirAll(migrations, 0700); err != nil {
		return report, err
	}
	stage, err := os.MkdirTemp(migrations, ".pending-")
	if err != nil {
		return report, err
	}
	defer os.RemoveAll(stage)
	if err := os.WriteFile(filepath.Join(stage, "manifest.json"), manifest, 0600); err != nil {
		return report, err
	}
	idHash := sha256.Sum256([]byte(request.MigrationID))
	archive := filepath.Join(migrations, hex.EncodeToString(idHash[:])+"-"+uuid.NewString())
	var archived bool
	err = w.Repo.Transaction(func(repo *repository.LocalWorkspaceRepository) error {
		for _, name := range []string{"canvas", "assets"} {
			doc := incoming[name]
			if doc == nil {
				continue
			}
			current, err := repo.State(name)
			if err != nil {
				return err
			}
			var base *workspaceDocument
			if current.Value != nil {
				base, err = parseWorkspaceState(name, *current.Value)
				if err != nil {
					return err
				}
			} else {
				copy := *doc
				copy.items = []json.RawMessage{}
				base = &copy
			}
			tombstones, err := repo.Tombstones(name)
			if err != nil {
				return err
			}
			ids := map[string]bool{}
			for _, item := range base.items {
				ids[workspaceEntityID(item)] = true
			}
			changed := current.Value == nil
			for _, item := range doc.items {
				id := workspaceEntityID(item)
				if ids[id] || tombstones["/state/"+doc.list+"/"+url.PathEscape(id)] {
					continue
				}
				base.items = append(base.items, item)
				ids[id] = true
				changed = true
			}
			if !changed {
				continue
			}
			value, err := base.value()
			if err != nil {
				return err
			}
			updated, err := repo.CompareAndSwap(name, value, current.Revision)
			if err != nil {
				return err
			}
			if !updated {
				return ErrLocalWorkspaceConflict
			}
		}
		var err error
		report, err = workspaceReport(repo)
		if err != nil {
			return err
		}
		report.MigrationID = request.MigrationID
		// Migration counts describe verified objects in this manifest, including implicit state references.
		report.FileCount, report.Bytes = int64(len(files)), 0
		for _, file := range files {
			report.Bytes += file.Bytes
		}
		rawReport, err := json.Marshal(report)
		if err != nil {
			return err
		}
		if err := os.WriteFile(filepath.Join(stage, "report.json"), rawReport, 0600); err != nil {
			return err
		}
		if err := os.Rename(stage, archive); err != nil {
			return err
		}
		archived = true
		return repo.CreateMigration(model.LocalWorkspaceMigration{MigrationID: request.MigrationID, Manifest: string(manifest), Report: string(rawReport), ArchivePath: archive})
	})
	if err != nil && archived {
		_ = os.RemoveAll(archive)
	}
	return report, err
}
