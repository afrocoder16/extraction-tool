import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "netlify-dist");
const work = join(root, "books", "wudase-mariam", "work");
const assets = join(root, "netlify", "review-assets", "work");

await rm(dist, { recursive: true, force: true });
await mkdir(join(dist, "work"), { recursive: true });

let html = await readFile(join(root, "engine", "review.html"), "utf8");
html = html.replace("</body>", '  <script type="module" src="/review-auth.js"></script>\n</body>');
await writeFile(join(dist, "index.html"), html, "utf8");

await build({
  entryPoints: [join(root, "netlify", "client", "auth.js")],
  outfile: join(dist, "review-auth.js"),
  bundle: true,
  format: "esm",
  platform: "browser",
  target: ["es2022"],
  minify: true,
});

await cp(assets, join(dist, "work"), { recursive: true });
await cp(join(work, "transcripts"), join(dist, "work", "transcripts"), { recursive: true });
for (const name of ["page_stats.json", "verification.json", "review.json"]) {
  try {
    await cp(join(work, name), join(dist, "work", name));
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}

const book = JSON.parse(await readFile(join(root, "books", "wudase-mariam", "book.json"), "utf8"));
book.hosting = "netlify";
book.image_extension = "webp";
await writeFile(join(dist, "book.json"), JSON.stringify(book, null, 2), "utf8");

await writeFile(join(dist, "_headers"), `/*
  Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
  Referrer-Policy: no-referrer
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY

/work/page_images/*
  Cache-Control: public, max-age=31536000, immutable

/work/debug/*
  Cache-Control: public, max-age=31536000, immutable

/work/transcripts/*
  Cache-Control: no-store

/work/page_stats.json
  Cache-Control: no-store

/work/verification.json
  Cache-Control: no-store

/work/review.json
  Cache-Control: no-store

/book.json
  Cache-Control: no-store

/index.html
  Cache-Control: no-store
`, "utf8");

console.log(`Built ${dist}`);
