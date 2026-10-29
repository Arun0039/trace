import assert from "node:assert/strict";
import test from "node:test";

import { formatProviderError, generateWithProvider } from "./ai_provider.mjs";

test("reports nested provider status codes", () => {
  assert.match(
    formatProviderError(
      new Error("request failed", { cause: { status: 403 } }),
      { provider: "google" },
    ),
    /rejected the API key or access permissions \(HTTP 403\)/,
  );
});

test("shows safe network details without exposing the API key", () => {
  const apiKey = "secret-google-key";
  const message = formatProviderError(
    Object.assign(new TypeError(`fetch failed for https://example.test/?key=${apiKey}`, {
      cause: { code: "ENOTFOUND" },
    }), { name: "NetworkError" }),
    { provider: "google", apiKey },
  );

  assert.match(message, /NetworkError: fetch failed/);
  assert.match(message, /key=\[redacted\]/);
  assert.match(message, /code ENOTFOUND/);
  assert.doesNotMatch(message, /secret-google-key/);
});

test("routes model requests through the configured provider abstraction", async () => {
  const request = {
    provider: "google",
    model: "gemini-2.5-flash",
    apiKey: "test-secret",
    base_url: "",
    system: "Extract requirements.",
    prompt: "Source BRD.",
    temperature: 0.1,
  };
  let captured;
  const response = await generateWithProvider(request, {
    providers: { google: (model, config) => ({ provider: "google", model, key: config.apiKey }) },
    generate: async (options) => {
      captured = options;
      return { text: "{\"requirements\":[]}" };
    },
  });

  assert.equal(response, "{\"requirements\":[]}");
  assert.deepEqual(captured.model, {
    provider: "google",
    model: "gemini-2.5-flash",
    key: "test-secret",
  });
  assert.equal(captured.system, "Extract requirements.");
  assert.equal(captured.prompt, "Source BRD.");
  assert.equal(captured.temperature, 0.1);
});

test("uses SDK structured JSON output when requested", async () => {
  let captured;
  const response = await generateWithProvider({
    provider: "google",
    model: "gemini-2.5-flash",
    apiKey: "test-secret",
    base_url: "",
    system: "Extract requirements.",
    prompt: "Source BRD.",
    temperature: 0.1,
    response_format: "json",
  }, {
    providers: { google: (model) => ({ model }) },
    generate: async (options) => {
      captured = options;
      return { output: { requirements: [{ title: "Login" }] } };
    },
  });

  assert.deepEqual(JSON.parse(response), { requirements: [{ title: "Login" }] });
  assert.ok(captured.output);
  assert.equal(captured.output.name, "json");
});

test("routes Ollama and compatible endpoints through the same SDK boundary", async () => {
  let captured;
  await generateWithProvider({
    provider: "openai-compatible",
    model: "custom-model",
    apiKey: "custom-secret",
    base_url: "https://models.example/v1",
    system: "",
    prompt: "test",
    temperature: 0,
  }, {
    providers: {
      "openai-compatible": (model, request) => ({
        model,
        baseURL: request.base_url,
        apiKey: request.apiKey,
      }),
    },
    generate: async (options) => {
      captured = options;
      return { text: "ok" };
    },
  });

  assert.equal(captured.model.model, "custom-model");
  assert.equal(captured.model.baseURL, "https://models.example/v1");
  assert.equal(captured.model.apiKey, "custom-secret");
});

test("routes Ollama without an API key through the same SDK boundary", async () => {
  let captured;
  await generateWithProvider({
    provider: "ollama",
    model: "llama3.1",
    apiKey: "",
    base_url: "http://127.0.0.1:11434/v1",
    system: "",
    prompt: "test",
    temperature: 0,
  }, {
    providers: {
      ollama: (model, request) => ({
        model,
        baseURL: request.base_url,
        apiKey: request.apiKey,
      }),
    },
    generate: async (options) => {
      captured = options;
      return { text: "ok" };
    },
  });

  assert.equal(captured.model.model, "llama3.1");
  assert.equal(captured.model.baseURL, "http://127.0.0.1:11434/v1");
  assert.equal(captured.model.apiKey, "");
});

test("constructs models for every SDK-backed provider without a network call", async () => {
  const requests = [
    { provider: "google", model: "gemini-2.5-flash", apiKey: "google-test-key", base_url: "" },
    { provider: "openai", model: "gpt-4o-mini", apiKey: "openai-test-key", base_url: "" },
    { provider: "anthropic", model: "claude-3-5-sonnet-latest", apiKey: "anthropic-test-key", base_url: "" },
    { provider: "ollama", model: "llama3.1", apiKey: "", base_url: "http://127.0.0.1:11434/v1" },
    {
      provider: "openai-compatible",
      model: "custom-model",
      apiKey: "compatible-test-key",
      base_url: "https://models.example/v1",
    },
  ];

  for (const request of requests) {
    const response = await generateWithProvider({
      ...request,
      system: "",
      prompt: "test",
      temperature: 0,
    }, {
      generate: async ({ model }) => {
        assert.ok(model);
        return { text: "ok" };
      },
    });
    assert.equal(response, "ok");
  }
});

test("rejects providers that are not enabled", async () => {
  await assert.rejects(
    generateWithProvider({
      provider: "mistral",
      model: "unknown",
      system: "",
      prompt: "test",
      temperature: 0,
    }),
    /Unsupported AI provider: mistral/,
  );
});

test("rejects unsupported structured response formats", async () => {
  await assert.rejects(
    generateWithProvider({
      provider: "google",
      model: "gemini-2.5-flash",
      apiKey: "test-secret",
      base_url: "",
      system: "",
      prompt: "test",
      temperature: 0,
      response_format: "xml",
    }),
    /Unsupported AI response format/,
  );
});
