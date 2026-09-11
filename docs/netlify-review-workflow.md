# Netlify review workflow

This guide explains how to publish the Wudase Mariam extraction review tool,
invite a proofreader, collect their edits, and bring those edits back into the
local extraction project.

## What this setup does

The project has two different parts:

| Part | Where it runs | Purpose |
| --- | --- | --- |
| Extraction engine | Your computer | Renders the PDF, detects red ink, transcribes pages, verifies them, and builds the final book JSON. |
| Review application | Netlify | Lets an invited proofreader compare scans with extracted text and correct it in a browser. |

Netlify does not run the OCR pipeline. It hosts the existing review data and
saves human corrections. No Anthropic API key is needed in Netlify.

The repository is:

<https://github.com/afrocoder16/extraction-tool>

## How the hosted application is built

Netlify reads `netlify.toml` from the repository root. The configured values
are:

| Setting | Value |
| --- | --- |
| Branch | `main` |
| Base directory | blank |
| Build command | `npm run build` |
| Publish directory | `netlify-dist` |
| Functions directory | `netlify/functions` |
| Node version | 22 |

The build script performs these steps:

1. Copies `engine/review.html` into the generated site.
2. Adds the browser login interface.
3. Copies the transcript baselines and verification data.
4. Copies the compact WebP page scans and red-ink overlays.
5. Bundles the Netlify Functions used for saving and exporting.

The original 300-DPI PNG working files are about 2 GB and remain on the local
computer. The hosted WebP review set is approximately 58 MB. `netlify-dist/` is
generated during every deployment and is intentionally not committed.

## First deployment

1. Sign in to Netlify.
2. Choose **Add new project** and **Import an existing project**.
3. Choose GitHub and select `afrocoder16/extraction-tool`.
4. Select the `main` branch.
5. Confirm the settings shown in the table above. Do not enter a base directory.
6. No environment variables are required.
7. Click **Deploy**.

After the first deployment, pushing a new commit to `main` automatically starts
a new Netlify deployment. Data already saved in Netlify Blobs persists across
those deployments.

## Enable login and invite the proofreader

After the site deploys:

1. Open the site configuration in Netlify.
2. Enable **Identity**.
3. Set registration to **Invite only**. Do not enable open registration.
4. Open the Identity user list.
5. Invite the proofreader's email address.
6. Send the proofreader the normal Netlify site URL.

The proofreader receives an invitation email. The invitation link opens the
review application and asks them to create a password. After that, they use the
normal site URL and sign in with their email and password.

If the invitation expires, remove or re-invite the user from Netlify and ask
them to use the newest email.

## What the proofreader should do

### Review a page

The scan is on the left and its transcript is on the right.

- **Original** shows the page scan.
- **Overlay** shows the red-ink detection. Green indicates detected red ink;
  magenta indicates a rejected ornament.
- **Split** compares the original and overlay in one view.
- Use the mouse wheel to zoom, drag to pan, and select **Fit** to reset the view.
- Hold `O` to show the overlay temporarily.
- Use the left and right arrow keys or the **Prev** and **Next** buttons to move
  between pages.

Blank pages and illustration pages are hidden by default because they do not
contain prayer text. The sidebar filter can show them when needed.

### Correct text

1. Click inside a text block and type the correction.
2. Select text and use **Mark red** or **Mark black** if the ink colour is wrong.
3. Use the block controls to change its type, add a block, or remove a block.
4. Resolve any suspected-misprint suggestions with **Accept** or **Keep as
   printed**.
5. Click the page-level **Save** button, or press `Ctrl+S`/`Cmd+S`.

A visible saved message confirms that the transcript reached Netlify. Do not
leave a page while it says that edits are unsaved.

The **Revert** button loads the checked-in original or baseline transcript. The
proofreader must press **Save** afterward to make the reverted version the
hosted version.

### Record the review decision

- Select **Approve** when the page is correct.
- Select **Needs fix** when work remains.
- Add an optional note explaining the decision.

Approval decisions and notes save separately from transcript text. Clicking
**Approve** does not save unsaved text edits, so the proofreader should always
save the transcript first.

The sidebar provides filters for flagged and unreviewed pages and displays the
number of completed content pages.

Some pages currently say **Not transcribed yet**. Those pages cannot be
proofread until the extraction engine produces their transcript; this is not a
Netlify error.

## Where edits are stored

The hosted application stores data in a site-wide Netlify Blobs store named
`extraction-review`:

- One object contains page approval decisions and notes.
- Each edited transcript is stored as a separate page object.
- Each saved object records the save time and the signed-in editor.

These changes are not Git commits and do not appear in the GitHub repository.
Therefore, running `git pull` does **not** download the proofreader's work.

The browser also keeps a local review fallback, but Netlify Blobs is the shared
copy used across devices.

For the safest workflow, use one proofreader at a time. Two people editing the
same page or changing review decisions simultaneously can overwrite one
another's latest save.

## Export the proofreader's work

When proofreading is complete:

1. Sign in to the deployed review application.
2. Select **Export all saved changes** in the sidebar.
3. The browser downloads `wudase_mariam_review_export.json`, normally into the
   user's Downloads folder.
4. Keep a backup of this file and copy it into the local project before merging.

The export contains:

- `book_id`
- export time and exporting user
- all hosted approval decisions and notes
- every transcript that was edited and saved on Netlify
- per-page save timestamps and editor identities

The export intentionally contains only hosted changes. It is not the complete,
assembled book JSON and does not repeat every unchanged baseline transcript.

## Bring the export back into the extraction project

Do not replace the finished book JSON directly with the Netlify export. Merge
the exported data into the working files first:

1. Copy each object under `transcripts` into its matching file under
   `books/wudase-mariam/work/transcripts/`. For example, `page_003` belongs in
   `page_003.json`.
2. Copy the exported review `entries` into
   `books/wudase-mariam/work/review.json` using the existing `{ "entries": ... }`
   shape.
3. Run verification again.
4. Build the book JSON again.
5. Commit and push the merged working transcripts if they should become the new
   hosted baseline.

From the repository root:

```powershell
python -m engine books/wudase-mariam --stage verify
python -m engine books/wudase-mariam --stage build --require-ready
```

The readiness check refuses to report success while required transcripts,
sections, or approvals are missing. The assembled local book file is:

```text
D:\projects\Tsion app\extract-tool\books\wudase-mariam\out\wudase_mariam.json
```

At the time this hosted reviewer was created, that file was not production
ready because 55 content pages still lacked canonical transcripts. Proofreading
the existing pages does not automatically transcribe those missing pages.

## Updating the hosted baseline

When local transcript or scan data changes:

1. Regenerate compact review assets only if page scans or overlays changed:

   ```powershell
   npm run build:assets
   ```

2. Test both parts of the project:

   ```powershell
   python -m unittest -v
   npm test
   npm run build
   ```

3. Commit the intended files and push `main`:

   ```powershell
   git add -A
   git commit -m "Update review baseline"
   git push
   ```

Netlify then deploys the new baseline automatically. Existing hosted edits in
Netlify Blobs remain in place and continue to take precedence over baseline
transcripts with the same page number. Export and merge existing hosted changes
before intentionally replacing that work.

## Security and access notes

- Saving transcripts, loading hosted edits, loading review decisions, and
  exporting changes all require an authenticated Identity user.
- The server validates page numbers, review statuses, transcript structure,
  request sizes, and same-origin write requests.
- Never add `ANTHROPIC_API_KEY`, Netlify tokens, passwords, or `.env` files to
  Git. The deployment does not need those credentials.
- The login screen controls normal access to the editor, but the static review
  scans and checked-in baseline JSON are public assets if someone knows their
  exact URLs. Do not use this deployment for confidential source material.
- Netlify's free plan has usage limits. Review the site's usage page when
  sharing it widely or keeping it online for a long period.

## Backups

Netlify Blob edits are separate from Git history. Export periodically during a
long review, not only at the very end. Keep dated copies such as:

```text
wudase_mariam_review_export_2026-09-11.json
```

After merging an export locally, commit the resulting transcript and review
files. That Git commit becomes the durable, auditable backup.

## Troubleshooting

### The deployment fails

Run the same build locally:

```powershell
npm install
npm test
npm run build
```

If those succeed, inspect the failed deployment's function-bundling section in
the Netlify log.

### The login screen appears, but nobody can sign in

Confirm that Identity is enabled, registration is set to Invite only, and the
email address is listed as an Identity user. The invited person must finish the
invitation flow before using the regular login form.

### Saving a transcript fails

Reload the page and sign in again. Then check the Netlify function log for the
`transcript` function. A rejected request normally means the session expired or
the transcript failed schema validation.

### Approval decisions do not appear on another device

Confirm both devices are signed in to the same deployed site. Reload the second
device after the first device finishes saving. Avoid simultaneous review
sessions.

### A page has an image but no text

If it says **Not transcribed yet**, run the local OCR/transcription stage for
that page, verify it, commit the new transcript, and push it to update Netlify.
The hosted application does not call the OCR API.

## Reference documentation

- Netlify Identity: <https://docs.netlify.com/manage/security/secure-access-to-sites/identity/overview/>
- Netlify Blobs: <https://docs.netlify.com/build/data-and-storage/netlify-blobs/>
- Netlify pricing: <https://www.netlify.com/pricing/>
