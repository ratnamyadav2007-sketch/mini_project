import { createHash } from "node:crypto";
import { cp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build, transform } from "esbuild";

const root = path.dirname(fileURLToPath(import.meta.url));
const output = path.join(root, "dist");
const assets = path.join(output, "assets");
const hash = content => createHash("sha256").update(content).digest("hex").slice(0, 12);
const bundles = {
  "app-shell": 'import "./app-loader.js";',
  auth: 'import "./js/bootstrap.js"; import "./auth.js";',
  bootstrap: 'import "./js/bootstrap.js";',
  "health-card": 'import "./js/bootstrap.js"; import "./health-card.js";',
  "api-client": 'import "./js/bootstrap.js"; import "./js/api-client-page.js";',
  "public-sos": 'import "./public-sos.js";',
};

async function writeAsset(name, extension, content) {
  const fileName = `${name}.${hash(content)}.${extension}`;
  await writeFile(path.join(assets, fileName), content);
  return `/assets/${fileName}`;
}

await rm(output, { recursive: true, force: true });
await mkdir(assets, { recursive: true });

const assetUrls = new Map();
const outputsToRewrite = [];
const privateFiles = new Set([
  "build.mjs",
  "package.json",
  "package-lock.json",
  "playwright.config.js",
  "README.md",
  "server.mjs",
]);

for (const [name, contents] of Object.entries(bundles)) {
  const result = await build({
    stdin: { contents, resolveDir: root, sourcefile: `${name}.entry.js` },
    bundle: true,
    splitting: true,
    format: "esm",
    platform: "browser",
    target: ["es2022"],
    plugins: [{
      name: "chart-esm",
      setup(buildContext) {
        buildContext.onResolve({ filter: /^\.\/chart\.umd\.js$/ }, args =>
          buildContext.resolve("chart.js/auto", { resolveDir: root, kind: args.kind }),
        );
      },
    }],
    minify: true,
    entryNames: "[name].[hash]",
    chunkNames: "chunks/[name].[hash]",
    outdir: assets,
    metafile: true,
    write: false,
  });
  let entryUrl;
  for (const file of result.outputFiles) {
    const relative = path.relative(assets, file.path).replaceAll(path.sep, "/");
    const bytes = file.contents;
    await mkdir(path.dirname(file.path), { recursive: true });
    await writeFile(file.path, bytes);
    if (!relative.startsWith("chunks/") && relative.endsWith(".js")) {
      entryUrl = `/assets/${relative}`;
      const nameForHtml = name === "app-shell" ? "app-shell.html"
        : name === "health-card" ? "health-card.html"
          : name === "api-client" ? "api-client-test.html" : name;
      assetUrls.set(nameForHtml, entryUrl);
    } else if (relative.endsWith(".js")) {
      outputsToRewrite.push(file.path);
    }
  }
  if (!entryUrl) throw new Error(`Bundler did not produce the ${name} entry point.`);
  outputsToRewrite.push(path.join(assets, path.basename(entryUrl)));
}

const sourceFiles = [];
async function collect(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (["node_modules", "dist", "e2e", "tests", "test-results", "playwright-report"].includes(entry.name)) continue;
    const filePath = path.join(directory, entry.name);
    if (entry.isDirectory()) await collect(filePath);
    else if (privateFiles.has(entry.name)) continue;
    else if (/\.(?:js|css)$/i.test(entry.name)) sourceFiles.push(filePath);
    else await cp(filePath, path.join(output, path.relative(root, filePath)), { recursive: true });
  }
}
await collect(root);

for (const filePath of sourceFiles.filter(file => file.endsWith(".css"))) {
  const source = await readFile(filePath, "utf8");
  const result = await transform(source, { loader: "css", minify: true, target: "es2022" });
  assetUrls.set(path.basename(filePath), await writeAsset(path.basename(filePath, ".css"), "css", result.code));
}

const workerSource = await readFile(path.join(root, "service-worker.js"), "utf8");
const workerResult = await transform(workerSource, { loader: "js", minify: true, target: "es2022" });
const workerUrl = await writeAsset("service-worker", "js", workerResult.code);
assetUrls.set("service-worker.js", workerUrl);

function rewriteReferences(content) {
  let rewritten = content;
  for (const [sourceName, target] of assetUrls) {
    if (!/\.(?:css|js|mjs|woff2?|svg)$/i.test(sourceName)) continue;
    const escapedName = sourceName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    rewritten = rewritten.replace(new RegExp(`(?<![A-Za-z0-9_-])(?:/|\\./)?${escapedName}(?![A-Za-z0-9_-])`, "g"), target);
  }
  return rewritten;
}

for (const filePath of outputsToRewrite) {
  const original = await readFile(filePath, "utf8");
  await writeFile(filePath, rewriteReferences(original));
}

for (const entry of await readdir(output, { withFileTypes: true })) {
  if (!entry.isFile() || !entry.name.endsWith(".html")) continue;
  const filePath = path.join(output, entry.name);
  let html = await readFile(filePath, "utf8");
  const entryUrl = assetUrls.get(entry.name)
    || (["register.html", "login.html", "reset.html"].includes(entry.name) && assetUrls.get("auth"))
    || (entry.name === "landing.html" && assetUrls.get("bootstrap"));
  if (entryUrl) {
    html = html.replace(/<script\b[^>]*\bsrc=["'][^"']+["'][^>]*>\s*<\/script>/gi, "");
    html = html.replace("</head>", `  <script type="module" src="${entryUrl}"></script>\n</head>`);
  }
  html = html.replace(/(<link\b[^>]*\bhref=["'])([^"']+\.css)(["'][^>]*>)/gi, (match, prefix, href, suffix) => {
    const target = assetUrls.get(path.posix.basename(href));
    return target ? `${prefix}${target}${suffix}` : match;
  });
  await writeFile(filePath, html);
}

for (const filePath of outputsToRewrite) {
  const source = await readFile(filePath, "utf8");
  const finalContent = rewriteReferences(source);
  if (finalContent !== source) await writeFile(filePath, finalContent);
}

console.log(`Built ${assetUrls.size} fingerprinted frontend assets into ${path.relative(root, output)}.`);
