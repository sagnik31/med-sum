// Command medsumctl manages organisations and staff accounts from the
// command line: the first admin, setup and reset links, and the development
// accounts. It talks to the database directly (DATABASE_URL).
//
//	medsumctl create-org   --name "Sunrise Hospital"
//	medsumctl create-user  --email a@b.in --name "Dr A" [--roles clinician,admin] [--org ID] [--registration-no X]
//	medsumctl create-admin --email a@b.in --name "A"    [--org ID]
//	medsumctl reset-link   --email a@b.in
//	medsumctl seed-dev     [--manifest ../scripts/evals/dataset/manifest.json]
//
// create-user, create-admin and reset-link print a one-time link to hand to
// the person; it is the only way to set a password, so no password is ever
// typed on a command line or known to whoever runs this.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"backend/internal/auth"
	"backend/internal/db"
)

const usage = `usage: medsumctl <command> [flags]

commands:
  create-org    create an organisation
  create-user   create a staff account and print its setup link
  create-admin  create-user with the admin role
  reset-link    print a password reset link for an account
  seed-dev      create the development accounts (MEDSUM_ENV=dev only)
`

func main() {
	if err := run(context.Background(), os.Args[1:], os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, "medsumctl:", err)
		os.Exit(1)
	}
}

func run(ctx context.Context, args []string, out io.Writer) error {
	if len(args) == 0 {
		return errors.New(usage)
	}
	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" {
		return errors.New("DATABASE_URL is required")
	}
	store, err := db.New(ctx, dsn)
	if err != nil {
		return err
	}
	defer store.Close()
	c := &ctl{store: store, out: out, now: time.Now, appURL: appURL()}

	cmd, rest := args[0], args[1:]
	switch cmd {
	case "create-org":
		return c.createOrg(ctx, rest)
	case "create-user":
		return c.createUser(ctx, rest, "")
	case "create-admin":
		return c.createUser(ctx, rest, db.RoleAdmin)
	case "reset-link":
		return c.resetLink(ctx, rest)
	case "seed-dev":
		return c.seedDev(ctx, rest)
	default:
		return fmt.Errorf("unknown command %q\n\n%s", cmd, usage)
	}
}

// appURL is where the frontend is served; links point at its
// set-password page.
func appURL() string {
	if v := os.Getenv("MEDSUM_APP_URL"); v != "" {
		return strings.TrimRight(v, "/")
	}
	return "http://localhost:5173"
}

type ctl struct {
	store  *db.Store
	out    io.Writer
	now    func() time.Time
	appURL string
}

func (c *ctl) createOrg(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("create-org", flag.ContinueOnError)
	name := fs.String("name", "", "organisation name (required)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if strings.TrimSpace(*name) == "" {
		return errors.New("--name is required")
	}
	org, err := c.store.CreateOrganization(ctx, strings.TrimSpace(*name))
	if err != nil {
		return err
	}
	fmt.Fprintf(c.out, "created organisation %q: %s\n", org.Name, org.ID)
	return nil
}

// pickOrg returns the organisation with the given ID, or the only one when
// id is empty.
func (c *ctl) pickOrg(ctx context.Context, id string) (string, error) {
	orgs, err := c.store.ListOrganizations(ctx)
	if err != nil {
		return "", err
	}
	if id != "" {
		for _, o := range orgs {
			if o.ID == id {
				return o.ID, nil
			}
		}
		return "", fmt.Errorf("no organisation with id %s", id)
	}
	switch len(orgs) {
	case 0:
		return "", errors.New("no organisation yet: run create-org first")
	case 1:
		return orgs[0].ID, nil
	default:
		return "", errors.New("several organisations exist: pass --org")
	}
}

func parseRoles(v string) ([]string, error) {
	var roles []string
	seen := map[string]bool{}
	for _, r := range strings.Split(v, ",") {
		r = strings.TrimSpace(r)
		if r == "" || seen[r] {
			continue
		}
		if r != db.RoleClinician && r != db.RoleAdmin {
			return nil, fmt.Errorf("unknown role %q (want clinician or admin)", r)
		}
		seen[r] = true
		roles = append(roles, r)
	}
	if len(roles) == 0 {
		return nil, errors.New("at least one role is required")
	}
	return roles, nil
}

func (c *ctl) createUser(ctx context.Context, args []string, forcedRole string) error {
	fs := flag.NewFlagSet("create-user", flag.ContinueOnError)
	email := fs.String("email", "", "email address (required)")
	name := fs.String("name", "", "full name (required)")
	rolesFlag := fs.String("roles", db.RoleClinician, "comma-separated roles: clinician, admin")
	orgID := fs.String("org", "", "organisation id (default: the only one)")
	regNo := fs.String("registration-no", "", "medical council registration number (not verified)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if forcedRole != "" {
		*rolesFlag = forcedRole
	}
	addr, err := auth.NormalizeEmail(*email)
	if err != nil {
		return err
	}
	if strings.TrimSpace(*name) == "" {
		return errors.New("--name is required")
	}
	roles, err := parseRoles(*rolesFlag)
	if err != nil {
		return err
	}
	org, err := c.pickOrg(ctx, *orgID)
	if err != nil {
		return err
	}
	nu := db.NewUser{OrgID: org, Email: addr, FullName: strings.TrimSpace(*name), Roles: roles}
	if v := strings.TrimSpace(*regNo); v != "" {
		nu.RegistrationNo = &v
	}
	u, err := c.store.CreateUser(ctx, nu)
	if err != nil {
		return err
	}
	link, err := c.issueLink(ctx, u.ID, db.PurposeSetup)
	if err != nil {
		return err
	}
	fmt.Fprintf(c.out, "created %s (%s) with roles %s\n", u.Email, u.FullName, strings.Join(u.Roles, ", "))
	fmt.Fprintf(c.out, "setup link, valid for %s and usable once:\n%s\n", auth.SetupLinkTTL, link)
	return nil
}

func (c *ctl) resetLink(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("reset-link", flag.ContinueOnError)
	email := fs.String("email", "", "email address (required)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	addr, err := auth.NormalizeEmail(*email)
	if err != nil {
		return err
	}
	u, err := c.store.GetUserByEmail(ctx, addr)
	if err != nil {
		return err
	}
	if u == nil {
		return fmt.Errorf("no account with email %s", addr)
	}
	if u.Status == db.StatusDisabled {
		return fmt.Errorf("%s is disabled", addr)
	}
	purpose := db.PurposeReset
	if u.Status == db.StatusInvited {
		purpose = db.PurposeSetup
	}
	link, err := c.issueLink(ctx, u.ID, purpose)
	if err != nil {
		return err
	}
	ttl := auth.ResetLinkTTL
	if purpose == db.PurposeSetup {
		ttl = auth.SetupLinkTTL
	}
	fmt.Fprintf(c.out, "%s link for %s, valid for %s and usable once:\n%s\n", purpose, addr, ttl, link)
	return nil
}

// issueLink stores a new one-time token for the account (replacing its
// unused ones) and returns the link.
func (c *ctl) issueLink(ctx context.Context, userID, purpose string) (string, error) {
	token, hash, err := auth.NewToken()
	if err != nil {
		return "", err
	}
	ttl := auth.SetupLinkTTL
	if purpose == db.PurposeReset {
		ttl = auth.ResetLinkTTL
	}
	now := c.now()
	if err := c.store.CreateOneTimeToken(ctx, db.OneTimeToken{
		TokenHash: hash, UserID: userID, Purpose: purpose, CreatedAt: now, ExpiresAt: now.Add(ttl),
	}); err != nil {
		return "", err
	}
	return auth.SetPasswordLink(c.appURL, token), nil
}

// Development accounts. The dataset loader (scripts/src/dataset/load.py)
// logs in with the same addresses and MEDSUM_DEV_PASSWORD.
const (
	devOrgName        = "Dev Hospital"
	devAdminEmail     = "dev-admin@med-sum.test"
	devClinicianEmail = "dev-clinician@med-sum.test"
)

// devPatientEmail is the interim account of a synthetic patient: until
// patients are records, each one's documents are uploaded by an
// account of its own.
func devPatientEmail(key string) string { return "patient-" + key + "@med-sum.test" }

type manifest struct {
	Patients []struct {
		Key  string `json:"key"`
		Name string `json:"name"`
	} `json:"patients"`
}

func (c *ctl) seedDev(ctx context.Context, args []string) error {
	fs := flag.NewFlagSet("seed-dev", flag.ContinueOnError)
	manifestPath := fs.String("manifest", "../scripts/evals/dataset/manifest.json",
		"synthetic dataset manifest; one account per patient")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if os.Getenv("MEDSUM_ENV") != "dev" {
		return errors.New("seed-dev only runs with MEDSUM_ENV=dev")
	}
	password := os.Getenv("MEDSUM_DEV_PASSWORD")
	type account struct {
		email, name string
		roles       []string
	}
	accounts := []account{
		{devAdminEmail, "Dev Admin", []string{db.RoleAdmin}},
		{devClinicianEmail, "Dr Dev Clinician", []string{db.RoleClinician}},
	}
	raw, err := os.ReadFile(*manifestPath)
	if err != nil {
		return fmt.Errorf("read the dataset manifest: %w", err)
	}
	var m manifest
	if err := json.Unmarshal(raw, &m); err != nil {
		return fmt.Errorf("parse the dataset manifest: %w", err)
	}
	for _, p := range m.Patients {
		accounts = append(accounts, account{devPatientEmail(p.Key), p.Name, []string{db.RoleClinician}})
	}
	for _, a := range accounts {
		// Checked against every account, since the rule looks at the email.
		if err := auth.CheckPassword(password, a.email); err != nil {
			return fmt.Errorf("MEDSUM_DEV_PASSWORD: %w", err)
		}
	}

	orgID, err := c.devOrg(ctx)
	if err != nil {
		return err
	}
	hasher, err := auth.NewHasher(auth.DefaultParams)
	if err != nil {
		return err
	}
	hash, err := hasher.Hash(password)
	if err != nil {
		return err
	}
	for _, a := range accounts {
		u, err := c.store.GetUserByEmail(ctx, a.email)
		if err != nil {
			return err
		}
		if u == nil {
			u, err = c.store.CreateUser(ctx, db.NewUser{OrgID: orgID, Email: a.email, FullName: a.name, Roles: a.roles})
			if err != nil {
				return err
			}
		}
		if err := c.store.SetPasswordHash(ctx, u.ID, hash, c.now()); err != nil {
			return err
		}
		if err := c.store.SetUserStatus(ctx, u.ID, db.StatusActive); err != nil {
			return err
		}
		fmt.Fprintf(c.out, "%-36s %s\n", a.email, strings.Join(a.roles, ", "))
	}
	fmt.Fprintf(c.out, "%d development accounts in %q; the password is MEDSUM_DEV_PASSWORD\n", len(accounts), devOrgName)
	return nil
}

func (c *ctl) devOrg(ctx context.Context) (string, error) {
	orgs, err := c.store.ListOrganizations(ctx)
	if err != nil {
		return "", err
	}
	for _, o := range orgs {
		if o.Name == devOrgName {
			return o.ID, nil
		}
	}
	org, err := c.store.CreateOrganization(ctx, devOrgName)
	if err != nil {
		return "", err
	}
	return org.ID, nil
}
