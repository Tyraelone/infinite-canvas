package model

import "time"

// Local workspace records belong to the trusted local runtime, independently of account sync.
type LocalWorkspaceState struct {
	Name      string `gorm:"primaryKey;size:16"`
	Value     string `gorm:"type:text;not null"`
	Revision  int64  `gorm:"not null"`
	UpdatedAt time.Time
}

type LocalWorkspaceFile struct {
	Key       string `gorm:"primaryKey;size:512"`
	SHA256    string `gorm:"size:64;not null;index"`
	Bytes     int64  `gorm:"not null"`
	MimeType  string `gorm:"size:255;not null"`
	CreatedAt time.Time
}

type LocalWorkspaceMigration struct {
	MigrationID string `gorm:"primaryKey;size:256"`
	Manifest    string `gorm:"type:text;not null"`
	Report      string `gorm:"type:text;not null"`
	ArchivePath string `gorm:"type:text;not null"`
	CreatedAt   time.Time
}

type LocalWorkspaceTombstone struct {
	Name       string `gorm:"primaryKey;size:16"`
	EntityPath string `gorm:"primaryKey;size:1024"`
	CreatedAt  time.Time
}

type LocalWorkspaceStateValue struct {
	Value    *string `json:"value"`
	Revision int64   `json:"revision"`
}

type LocalWorkspaceUpload struct {
	Key      string `json:"key"`
	SHA256   string `json:"sha256"`
	Bytes    int64  `json:"bytes"`
	MimeType string `json:"mimeType"`
	URL      string `json:"url"`
}

type LocalWorkspaceImportFile struct {
	Key    string `json:"key"`
	SHA256 string `json:"sha256"`
}

type LocalWorkspaceImportRequest struct {
	MigrationID string                     `json:"migrationId"`
	States      map[string]*string         `json:"states"`
	Files       []LocalWorkspaceImportFile `json:"files"`
}

type LocalWorkspaceReport struct {
	MigrationID  string           `json:"migrationId,omitempty"`
	ProjectCount int              `json:"projectCount"`
	AssetCount   int              `json:"assetCount"`
	FileCount    int64            `json:"fileCount"`
	Bytes        int64            `json:"bytes"`
	Revisions    map[string]int64 `json:"revisions"`
	StorageRoot  string           `json:"storageRoot,omitempty"`
}
