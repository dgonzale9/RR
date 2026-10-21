const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function loadUploader(fetch) {
  const context = vm.createContext({
    fetch, URL, Blob, DOMException, location: { origin: "https://app.test" },
    setTimeout: (callback) => callback(),
  });
  const source = fs.readFileSync(path.join(__dirname, "../../static/resource_registry/admin/direct_intake_upload.js"), "utf8");
  vm.runInContext(source + "\nglobalThis.Uploader = BackendBlockUploader;", context);
  return context.Uploader;
}

function createUploader(fetch, overrides = {}) {
  const Uploader = loadUploader(fetch);
  const checkpoint = { completedBlockIndexes: [] };
  const uploader = new Uploader({
    file: new Blob(["abcdefg"]),
    authorization: { method: "backend_blocks_v1", url: "/intakes/test/blocks/0/", recommended_chunk_size_bytes: 3 },
    checkpoint, onProgress: () => {}, signal: new AbortController().signal,
    csrfToken: "test-csrf", ...overrides,
  });
  uploader.setCheckpointStore({ save: () => {} });
  return { uploader, checkpoint };
}

test("sends bounded blocks only to authenticated application endpoints", async () => {
  const requests = [];
  const { uploader, checkpoint } = createUploader(async (url, options) => {
    requests.push({ url: url.href, options, body: await options.body.text() });
    return new Response(null, { status: 204 });
  });
  await uploader.upload();
  assert.deepEqual(requests.map((request) => request.body), ["abc", "def", "g"]);
  assert.deepEqual(requests.map((request) => request.url), [0, 1, 2].map((index) => `https://app.test/intakes/test/blocks/${index}/`));
  for (const { options } of requests) {
    assert.equal(options.method, "PUT");
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.headers["X-CSRFToken"], "test-csrf");
    assert.equal(options.redirect, "error");
  }
  assert.equal(checkpoint.completedBlockIndexes.length, 3);
});

test("retries transient provider failures without acknowledging failed blocks", async () => {
  let attempts = 0;
  const { uploader, checkpoint } = createUploader(async () => {
    attempts += 1;
    return attempts === 1
      ? new Response('{"code":"UPLOAD_PROVIDER_TRANSFER_FAILED"}', { status: 503 })
      : new Response(null, { status: 204 });
  });
  await uploader.upload();
  assert.equal(attempts, 4);
  assert.equal(checkpoint.completedBlockIndexes.length, 3);
});

test("permission failures stop immediately and preserve no false checkpoint", async () => {
  let attempts = 0;
  const { uploader, checkpoint } = createUploader(async () => {
    attempts += 1;
    return new Response(null, { status: 403 });
  });
  await assert.rejects(uploader.upload(), { code: "INTAKE_AUTHENTICATION_REQUIRED" });
  assert.equal(attempts, 1);
  assert.equal(checkpoint.completedBlockIndexes.length, 0);
});

test("cancellation prevents subsequent requests", async () => {
  const controller = new AbortController();
  controller.abort();
  const { uploader } = createUploader(() => assert.fail("unexpected request"), { signal: controller.signal });
  await assert.rejects(uploader.upload(), { name: "AbortError" });
});

test("rejects storage URLs instead of forwarding credentials off origin", async () => {
  const { uploader } = createUploader(() => assert.fail("unexpected request"), {
    authorization: { method: "backend_blocks_v1", url: "https://storage.test/blocks/0/", recommended_chunk_size_bytes: 3 },
  });
  await assert.rejects(uploader.upload(), { code: "INTAKE_UPLOAD_PROTOCOL_MISMATCH" });
});
