import { cp, mkdir, readdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = path.dirname(fileURLToPath(import.meta.url));
const outputDirectory = path.join(frontendRoot, "dist");
const configuredApiUrl = (process.env.QA_API_BASE_URL || process.argv[2] || "").trim();

if (!configuredApiUrl) {
  throw new Error("Set QA_API_BASE_URL to the HTTPS origin of the Render API.");
}

let apiOrigin;
try {
  const parsedUrl = new URL(configuredApiUrl);
  if (
    parsedUrl.protocol !== "https:"
    || !parsedUrl.hostname
    || parsedUrl.username
    || parsedUrl.password
    || !["", "/"].includes(parsedUrl.pathname)
    || parsedUrl.search
    || parsedUrl.hash
  ) {
    throw new Error();
  }
  apiOrigin = parsedUrl.origin;
} catch {
  throw new Error("QA_API_BASE_URL must be an HTTPS origin without credentials or a path.");
}

await rm(outputDirectory, { recursive: true, force: true });
await mkdir(outputDirectory, { recursive: true });
for (const entry of await readdir(frontendRoot, { withFileTypes: true })) {
  if (["dist", "node_modules", "__pycache__", "build_pages.mjs", ".DS_Store"].includes(entry.name)) {
    continue;
  }
  if (entry.isFile() && (entry.name.endsWith(".py") || entry.name.endsWith(".pyc"))) {
    continue;
  }
  const sourcePath = path.join(frontendRoot, entry.name);
  await cp(sourcePath, path.join(outputDirectory, entry.name), {
    recursive: true,
    filter(copyPath) {
      const relativePath = path.relative(frontendRoot, copyPath);
      const segments = relativePath.split(path.sep);
      const filename = path.basename(copyPath);
      return !segments.some(segment => [
        "dist",
        "node_modules",
        "__pycache__",
      ].includes(segment))
        && !filename.endsWith(".py")
        && !filename.endsWith(".pyc")
        && filename !== "build_pages.mjs"
        && filename !== ".DS_Store";
    },
  });
}
await writeFile(
  path.join(outputDirectory, "config.js"),
  `window.QA_API_BASE_URL = ${JSON.stringify(apiOrigin)};\n`,
  "utf8",
);
await writeFile(
  path.join(outputDirectory, "_headers"),
  [
    "/*",
    "  X-Content-Type-Options: nosniff",
    "  X-Frame-Options: DENY",
    "  Referrer-Policy: no-referrer",
    "  Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()",
    `  Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self' ${apiOrigin}; frame-ancestors 'none'; base-uri 'self'; form-action 'self'`,
    "",
    "/config.js",
    "  Cache-Control: no-store",
    "",
  ].join("\n"),
  "utf8",
);

console.log(`Built Cloudflare Pages frontend in ${outputDirectory}`);
