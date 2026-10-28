package repository

import (
	"errors"

	"github.com/tigerowo/infinite-canvas/model"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

type LocalWorkspaceRepository struct{ db *gorm.DB }

func NewLocalWorkspace(db *gorm.DB) *LocalWorkspaceRepository {
	return &LocalWorkspaceRepository{db: db}
}

func (r *LocalWorkspaceRepository) Transaction(fn func(*LocalWorkspaceRepository) error) error {
	return r.db.Transaction(func(tx *gorm.DB) error { return fn(NewLocalWorkspace(tx)) })
}

func (r *LocalWorkspaceRepository) State(name string) (model.LocalWorkspaceStateValue, error) {
	var state model.LocalWorkspaceState
	err := r.db.Take(&state, "name = ?", name).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return model.LocalWorkspaceStateValue{}, nil
	}
	if err != nil {
		return model.LocalWorkspaceStateValue{}, err
	}
	return model.LocalWorkspaceStateValue{Value: &state.Value, Revision: state.Revision}, nil
}

// CompareAndSwap returns false when another writer has already advanced the revision.
func (r *LocalWorkspaceRepository) CompareAndSwap(name, value string, revision int64) (bool, error) {
	if revision == 0 {
		result := r.db.Clauses(clause.OnConflict{DoNothing: true}).Create(&model.LocalWorkspaceState{Name: name, Value: value, Revision: 1})
		return result.RowsAffected == 1, result.Error
	}
	result := r.db.Model(&model.LocalWorkspaceState{}).Where("name = ? AND revision = ?", name, revision).Updates(map[string]any{"value": value, "revision": revision + 1})
	return result.RowsAffected == 1, result.Error
}

func (r *LocalWorkspaceRepository) File(key string) (model.LocalWorkspaceFile, error) {
	var file model.LocalWorkspaceFile
	err := r.db.Take(&file, "key = ?", key).Error
	return file, err
}

func (r *LocalWorkspaceRepository) CreateFile(file model.LocalWorkspaceFile) (bool, error) {
	result := r.db.Clauses(clause.OnConflict{DoNothing: true}).Create(&file)
	return result.RowsAffected == 1, result.Error
}

func (r *LocalWorkspaceRepository) FileTotals() (int64, int64, error) {
	var totals struct {
		Count int64
		Bytes int64
	}
	err := r.db.Model(&model.LocalWorkspaceFile{}).Select("COUNT(*) AS count, COALESCE(SUM(bytes), 0) AS bytes").Scan(&totals).Error
	return totals.Count, totals.Bytes, err
}

func (r *LocalWorkspaceRepository) Tombstones(name string) (map[string]bool, error) {
	var rows []model.LocalWorkspaceTombstone
	if err := r.db.Where("name = ?", name).Find(&rows).Error; err != nil {
		return nil, err
	}
	paths := make(map[string]bool, len(rows))
	for _, row := range rows {
		paths[row.EntityPath] = true
	}
	return paths, nil
}

func (r *LocalWorkspaceRepository) AddTombstones(name string, paths []string) error {
	if len(paths) == 0 {
		return nil
	}
	rows := make([]model.LocalWorkspaceTombstone, 0, len(paths))
	for _, path := range paths {
		rows = append(rows, model.LocalWorkspaceTombstone{Name: name, EntityPath: path})
	}
	return r.db.Clauses(clause.OnConflict{DoNothing: true}).Create(&rows).Error
}

func (r *LocalWorkspaceRepository) Migration(id string) (*model.LocalWorkspaceMigration, error) {
	var migration model.LocalWorkspaceMigration
	err := r.db.Take(&migration, "migration_id = ?", id).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, nil
	}
	return &migration, err
}

func (r *LocalWorkspaceRepository) CreateMigration(migration model.LocalWorkspaceMigration) error {
	return r.db.Create(&migration).Error
}
