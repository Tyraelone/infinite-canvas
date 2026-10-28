package handler

import (
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/tigerowo/infinite-canvas/model"
	"github.com/tigerowo/infinite-canvas/repository"
	"github.com/tigerowo/infinite-canvas/service"
)

// RegisterLocalWorkspaceRoutes exposes the internal runtime API only in explicitly enabled local mode.
func RegisterLocalWorkspaceRoutes(r gin.IRouter) {
	registerLocalWorkspaceRoutes(r, func() (*service.LocalWorkspace, error) {
		db, err := repository.DB()
		if err != nil {
			return nil, err
		}
		return service.NewLocalWorkspace(repository.NewLocalWorkspace(db), filepath.Join("data", "workspace")), nil
	})
}

func registerLocalWorkspaceRoutes(r gin.IRouter, workspace func() (*service.LocalWorkspace, error)) {
	if os.Getenv("CANVAS_LOCAL_MODE") != "1" {
		return
	}
	group := r.Group("/api/local-workspace", func(c *gin.Context) {
		if !service.LocalWorkspaceAuthorized(c.GetHeader("Authorization")) {
			FailWithStatus(c.Writer, http.StatusUnauthorized, "本机工作区凭据无效")
			c.Abort()
			return
		}
		c.Next()
	})
	run := func(action func(*gin.Context, *service.LocalWorkspace) error) gin.HandlerFunc {
		return func(c *gin.Context) {
			w, err := workspace()
			if err == nil {
				err = action(c, w)
			}
			if err == nil {
				return
			}
			switch {
			case errors.Is(err, service.ErrLocalWorkspaceLocked):
				FailWithStatus(c.Writer, http.StatusLocked, err.Error())
			case errors.Is(err, service.ErrLocalWorkspaceConflict):
				FailWithStatus(c.Writer, http.StatusConflict, err.Error())
			case errors.Is(err, service.ErrLocalWorkspaceInvalid):
				FailWithStatus(c.Writer, http.StatusBadRequest, err.Error())
			case errors.Is(err, service.ErrLocalWorkspaceNotFound):
				FailWithStatus(c.Writer, http.StatusNotFound, err.Error())
			default:
				log.Printf("local workspace: %v", err)
				FailWithStatus(c.Writer, http.StatusInternalServerError, "本机工作区操作失败")
			}
		}
	}
	group.POST("/lease", run(func(c *gin.Context, w *service.LocalWorkspace) error {
		var request struct {
			Revision *int64 `json:"revision"`
		}
		if err := c.ShouldBindJSON(&request); err != nil || request.Revision == nil {
			return service.ErrLocalWorkspaceInvalid
		}
		if err := w.AcquireWriterLease(c.GetHeader("X-Workspace-Client"), *request.Revision); err != nil {
			return err
		}
		OK(c.Writer, gin.H{"ttlSeconds": service.LocalWorkspaceLeaseSeconds})
		return nil
	}))
	group.GET("/state/:name", run(func(c *gin.Context, w *service.LocalWorkspace) error {
		state, err := w.State(c.Param("name"))
		if err != nil {
			return err
		}
		c.Header("Cache-Control", "no-store")
		OK(c.Writer, state)
		return nil
	}))
	group.PUT("/state/:name", run(func(c *gin.Context, w *service.LocalWorkspace) error {
		var request struct {
			Value    *string `json:"value"`
			Revision *int64  `json:"revision"`
		}
		raw, err := io.ReadAll(http.MaxBytesReader(c.Writer, c.Request.Body, 128<<20))
		if err != nil || json.Unmarshal(raw, &request) != nil || request.Value == nil || request.Revision == nil {
			return service.ErrLocalWorkspaceInvalid
		}
		revision, err := w.PutState(c.Param("name"), *request.Value, *request.Revision, c.GetHeader("X-Workspace-Client"))
		if err != nil {
			return err
		}
		OK(c.Writer, gin.H{"revision": revision})
		return nil
	}))
	group.POST("/files/*key", run(func(c *gin.Context, w *service.LocalWorkspace) error {
		file, err := w.Upload(strings.TrimPrefix(c.Param("key"), "/"), c.GetHeader("Content-Type"), c.Request.Body, c.GetHeader("X-Content-SHA256"))
		if err != nil {
			return err
		}
		OK(c.Writer, file)
		return nil
	}))
	file := run(func(c *gin.Context, w *service.LocalWorkspace) error {
		metadata, path, err := w.File(strings.TrimPrefix(c.Param("key"), "/"))
		if err != nil {
			return err
		}
		file, err := os.Open(path)
		if err != nil {
			return err
		}
		defer file.Close()
		c.Header("Content-Type", metadata.MimeType)
		c.Header("X-Content-Type-Options", "nosniff")
		c.Header("Content-Security-Policy", "sandbox; default-src 'none'; base-uri 'none'; form-action 'none'")
		c.Header("Content-Disposition", "inline")
		c.Header("X-Content-SHA256", metadata.SHA256)
		c.Header("X-Content-Bytes", strconv.FormatInt(metadata.Bytes, 10))
		c.Header("ETag", `"`+metadata.SHA256+`"`)
		c.Header("Cache-Control", "private, max-age=31536000, immutable")
		http.ServeContent(c.Writer, c.Request, "", metadata.CreatedAt, file)
		return nil
	})
	group.GET("/files/*key", file)
	group.HEAD("/files/*key", file)
	group.POST("/import", run(func(c *gin.Context, w *service.LocalWorkspace) error {
		raw, err := io.ReadAll(http.MaxBytesReader(c.Writer, c.Request.Body, 128<<20))
		if err != nil {
			return service.ErrLocalWorkspaceInvalid
		}
		var request model.LocalWorkspaceImportRequest
		if json.Unmarshal(raw, &request) != nil {
			return service.ErrLocalWorkspaceInvalid
		}
		report, err := w.Import(request, raw, c.GetHeader("X-Workspace-Client"))
		if err != nil {
			return err
		}
		OK(c.Writer, report)
		return nil
	}))
	group.GET("/status", run(func(c *gin.Context, w *service.LocalWorkspace) error {
		report, err := w.Status()
		if err != nil {
			return err
		}
		c.Header("Cache-Control", "no-store")
		OK(c.Writer, report)
		return nil
	}))
}
