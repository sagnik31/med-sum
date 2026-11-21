package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
)

// orphanUploads lists the files under the uploads directory that no
// document row names: a file written by an upload whose row was never
// inserted (the server stopped in between). Such a file holds patient data
// that no delete or purge reaches (docs/adr/0008-patient-data-protection.md),
// so an operator checks it and removes it by hand. Nothing is deleted here.
func (c *ctl) orphanUploads(ctx context.Context, args []string) error {
	fl := flag.NewFlagSet("orphan-uploads", flag.ContinueOnError)
	dir := fl.String("dir", envOr("UPLOADS_DIR", "uploads"), "the uploads directory")
	if err := fl.Parse(args); err != nil {
		return err
	}
	known, err := c.store.StoragePaths(ctx)
	if err != nil {
		return err
	}
	found, err := orphans(*dir, known)
	if err != nil {
		return err
	}
	for _, f := range found {
		fmt.Fprintln(c.out, f)
	}
	fmt.Fprintf(c.out, "%d file(s) in %s that no document names\n", len(found), *dir)
	return nil
}

// orphans returns the files under dir, as absolute paths in order, that
// are not among known (the documents' absolute storage paths).
func orphans(dir string, known []string) ([]string, error) {
	root, err := filepath.Abs(dir)
	if err != nil {
		return nil, err
	}
	names := make(map[string]bool, len(known))
	for _, k := range known {
		names[filepath.Clean(k)] = true
	}
	var out []string
	err = filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			if errors.Is(err, fs.ErrNotExist) && path == root {
				return fs.SkipAll // no uploads yet
			}
			return err
		}
		if !d.IsDir() && !names[filepath.Clean(path)] {
			out = append(out, path)
		}
		return nil
	})
	sort.Strings(out)
	return out, err
}

func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
