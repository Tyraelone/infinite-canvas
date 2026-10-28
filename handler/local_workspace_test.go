package handler

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/libtnb/sqlite"
	"github.com/tigerowo/infinite-canvas/model"
	"github.com/tigerowo/infinite-canvas/repository"
	"github.com/tigerowo/infinite-canvas/service"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func localWorkspaceTestRouter(t *testing.T) *gin.Engine {
	t.Helper()
	t.Setenv("CANVAS_LOCAL_MODE", "1")
	t.Setenv("CANVAS_LOCAL_TOKEN", "test-token")
	root := t.TempDir()
	db, err := gorm.Open(sqlite.Open(filepath.Join(root, "test.db")), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	if err != nil {
		t.Fatal(err)
	}
	connection, _ := db.DB()
	t.Cleanup(func() { _ = connection.Close() })
	if err := db.AutoMigrate(&model.LocalWorkspaceState{}, &model.LocalWorkspaceFile{}, &model.LocalWorkspaceMigration{}, &model.LocalWorkspaceTombstone{}); err != nil {
		t.Fatal(err)
	}
	r := gin.New()
	registerLocalWorkspaceRoutes(r, func() (*service.LocalWorkspace, error) {
		return service.NewLocalWorkspace(repository.NewLocalWorkspace(db), filepath.Join(root, "workspace")), nil
	})
	return r
}

func workspaceHTTP(r http.Handler, method, path, body string, headers map[string]string) *httptest.ResponseRecorder {
	request := httptest.NewRequest(method, path, strings.NewReader(body))
	request.Header.Set("Authorization", "Bearer test-token")
	for name, value := range headers {
		request.Header.Set(name, value)
	}
	response := httptest.NewRecorder()
	r.ServeHTTP(response, request)
	return response
}

func TestLocalWorkspaceRoutesRequireEnabledModeAndNonemptyToken(t *testing.T) {
	t.Setenv("CANVAS_LOCAL_MODE", "0")
	r := gin.New()
	RegisterLocalWorkspaceRoutes(r)
	if response := workspaceHTTP(r, "GET", "/api/local-workspace/status", "", nil); response.Code != 404 {
		t.Fatalf("disabled route exposed: %d", response.Code)
	}
	r = localWorkspaceTestRouter(t)
	for _, token := range []string{"", "Bearer incorrect", "test-token"} {
		response := workspaceHTTP(r, "GET", "/api/local-workspace/status", "", map[string]string{"Authorization": token})
		if response.Code != 401 {
			t.Fatalf("bad token authorized: %q %d", token, response.Code)
		}
	}
	t.Setenv("CANVAS_LOCAL_TOKEN", "")
	if response := workspaceHTTP(r, "GET", "/api/local-workspace/status", "", map[string]string{"Authorization": "Bearer "}); response.Code != 401 {
		t.Fatalf("empty configured token authorized: %d", response.Code)
	}
}

func TestLocalWorkspaceHTTPStateRevisionConflictAndEnvelope(t *testing.T) {
	r := localWorkspaceTestRouter(t)
	response := workspaceHTTP(r, "GET", "/api/local-workspace/state/canvas", "", nil)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"value":null`) || !strings.Contains(response.Body.String(), `"revision":0`) {
		t.Fatalf("empty state: %d %s", response.Code, response.Body)
	}
	body := `{"value":"{\"state\":{\"projects\":[{\"id\":\"p\"}]}}","revision":0}`
	response = workspaceHTTP(r, "PUT", "/api/local-workspace/state/canvas", body, nil)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"revision":1`) {
		t.Fatalf("put: %d %s", response.Code, response.Body)
	}
	response = workspaceHTTP(r, "PUT", "/api/local-workspace/state/canvas", body, nil)
	if response.Code != 409 || !strings.Contains(response.Body.String(), `"code":1`) {
		t.Fatalf("CAS: %d %s", response.Code, response.Body)
	}
	response = workspaceHTTP(r, "GET", "/api/local-workspace/state/credentials", "", nil)
	if response.Code != 400 {
		t.Fatalf("invalid state name accepted: %d", response.Code)
	}
}

func TestLocalWorkspaceHTTPRejectsMalformedTrailingJSONWithoutPublishing(t *testing.T) {
	r := localWorkspaceTestRouter(t)
	body := `{"value":"{\"state\":{\"projects\":[{\"id\":\"p\"}]}}","revision":0} {"unexpected":true}`
	response := workspaceHTTP(r, "PUT", "/api/local-workspace/state/canvas", body, nil)
	if response.Code != 400 {
		t.Fatalf("malformed JSON accepted: %d %s", response.Code, response.Body)
	}
	response = workspaceHTTP(r, "GET", "/api/local-workspace/state/canvas", "", nil)
	if !strings.Contains(response.Body.String(), `"value":null`) || !strings.Contains(response.Body.String(), `"revision":0`) {
		t.Fatalf("malformed request published state: %s", response.Body)
	}
}

func TestLocalWorkspaceHTTPMediaRangeHeadAndInlineSafety(t *testing.T) {
	r := localWorkspaceTestRouter(t)
	path := "/api/local-workspace/files/video%3Aone"
	response := workspaceHTTP(r, "POST", path, "0123456789", map[string]string{"Content-Type": "video/mp4"})
	if response.Code != 200 {
		t.Fatalf("upload: %d %s", response.Code, response.Body)
	}
	var body struct {
		Data model.LocalWorkspaceUpload `json:"data"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil || body.Data.Bytes != 10 || body.Data.Key != "video:one" {
		t.Fatalf("metadata: %+v %v", body, err)
	}
	response = workspaceHTTP(r, "POST", path, "0123456789", map[string]string{"Content-Type": "application/octet-stream"})
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"mimeType":"video/mp4"`) {
		t.Fatalf("same key and bytes not idempotent: %d %s", response.Code, response.Body)
	}
	response = workspaceHTTP(r, "GET", path, "", map[string]string{"Range": "bytes=2-5"})
	if response.Code != 206 || response.Body.String() != "2345" || response.Header().Get("Content-Range") != "bytes 2-5/10" || response.Header().Get("Content-Type") != "video/mp4" {
		t.Fatalf("range: %d %s %+v", response.Code, response.Body, response.Header())
	}
	response = workspaceHTTP(r, "GET", path, "", map[string]string{"Range": "bytes=50-60"})
	if response.Code != 416 {
		t.Fatalf("unsatisfiable range accepted: %d", response.Code)
	}
	response = workspaceHTTP(r, "HEAD", path, "", nil)
	if response.Code != 200 || response.Body.Len() != 0 || response.Header().Get("Content-Length") != "10" || response.Header().Get("X-Content-SHA256") != body.Data.SHA256 {
		t.Fatalf("HEAD: %d %+v", response.Code, response.Header())
	}
	response = workspaceHTTP(r, "POST", path, "replacement", nil)
	if response.Code != 409 {
		t.Fatalf("immutable key overwritten: %d", response.Code)
	}
	workspaceHTTP(r, "POST", "/api/local-workspace/files/doc%3Aone", "<script>fetch('/api')</script>", map[string]string{"Content-Type": "text/html"})
	response = workspaceHTTP(r, "GET", "/api/local-workspace/files/doc%3Aone", "", nil)
	if response.Header().Get("X-Content-Type-Options") != "nosniff" || !strings.Contains(response.Header().Get("Content-Security-Policy"), "sandbox") {
		t.Fatalf("inline document has same-origin execution privileges: %+v", response.Header())
	}
}
