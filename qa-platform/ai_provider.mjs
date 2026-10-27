import { createGoogle } from "@ai-sdk/google";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { generateText, Output } from "ai";
import { pathToFileURL } from "node:url";

const providerFactories = {
  google: (model, request) => createGoogle({ apiKey: request.apiKey })(model),
  ollama: (model, request) => createOpenAICompatible({
    name: "ollama",
    baseURL: request.base_url,
    ...(request.apiKey ? { apiKey: request.apiKey } : {}),
  })(model),
  "openai-compatible": (model, request) => createOpenAICompatible({
    name: "openai-compatible",
    baseURL: request.base_url,
    apiKey: request.apiKey,
  })(model),
};

export async function generateWithProvider(
  request,
  { providers = providerFactories, generate = generateText } = {},
) {
  const { provider, model, system, prompt, temperature } = request;
  const responseFormat = request.response_format;
  const createModel = providers[provider];
  if (!createModel) {
    throw new Error(`Unsupported AI provider: ${provider}`);
  }
  if (responseFormat !== undefined && responseFormat !== "json") {
    throw new Error("Unsupported AI response format. Use json for structured output.");
  }
  if (
    typeof model !== "string" ||
    !model ||
    typeof prompt !== "string" ||
    !prompt ||
    typeof request.apiKey !== "string" ||
    (provider !== "ollama" && !request.apiKey) ||
    (provider !== "google" && typeof request.base_url !== "string")
  ) {
    throw new Error("The AI provider request is missing its model or prompt.");
  }

  const response = await generate({
    model: createModel(model, request),
    system,
    prompt,
    temperature,
    ...(responseFormat === "json"
      ? { output: Output.json({ name: "structured_response" }) }
      : {}),
    abortSignal: AbortSignal.timeout(600_000),
  });
  return responseFormat === "json" ? JSON.stringify(response.output) : response.text;
}

async function runCli() {
  let input = "";
  for await (const chunk of process.stdin) {
    input += chunk;
    if (Buffer.byteLength(input, "utf8") > 2 * 1024 * 1024) {
      throw new Error("The AI provider request exceeds the 2 MB input limit.");
    }
  }

  try {
    const request = JSON.parse(input);
    const text = await generateWithProvider(request);
    process.stdout.write(JSON.stringify({ text }));
  } catch (error) {
    let request;
    try {
      request = JSON.parse(input);
    } catch {
      request = {};
    }
    const message = formatProviderError(error, request);
    process.stdout.write(JSON.stringify({ error: message }));
    process.exitCode = 1;
  }
}

export function formatProviderError(error, request = {}) {
  const label = providerLabel(request.provider);
  const statusCode = errorStatusCode(error);
  if (statusCode === 401 || statusCode === 403) {
    return `${label} rejected the API key or access permissions (HTTP ${statusCode}).`;
  }
  if (statusCode === 400) {
    return `${label} rejected the request (HTTP 400). Check the API key, model, and request.`;
  }
  if (statusCode === 404) {
    return `${label} could not find the configured model. Check the model name and provider URL.`;
  }
  if (statusCode === 429) {
    return `${label} rate or quota limit reached. Try again later or check the provider quota.`;
  }
  if (error?.message?.startsWith("Unsupported AI provider:")) {
    return error.message;
  }
  if (error?.message?.startsWith("The AI provider request")) {
    return error.message;
  }
  if (statusCode) {
    return `${label} request failed with HTTP ${statusCode}.`;
  }

  const detail = errorDetail(error, request.apiKey);
  return `${label} request failed${detail ? ` (${detail})` : ""}. Check the provider configuration and network connection.`;
}

function errorStatusCode(error) {
  const visited = new Set();
  let current = error;
  while (current && typeof current === "object" && !visited.has(current)) {
    visited.add(current);
    const statusCode = current.statusCode ?? current.status ?? current.response?.status;
    if (Number.isInteger(statusCode)) {
      return statusCode;
    }
    current = current.cause;
  }
  return 0;
}

function errorDetail(error, apiKey) {
  if (!error || typeof error !== "object") {
    return "";
  }
  const parts = [];
  if (typeof error.name === "string" && error.name !== "Error") {
    parts.push(error.name);
  }
  if (typeof error.message === "string" && error.message.trim()) {
    parts.push(error.message.trim());
  }
  const visited = new Set();
  let current = error;
  while (current && typeof current === "object" && !visited.has(current)) {
    visited.add(current);
    if (typeof current.code === "string") {
      parts.push(`code ${current.code}`);
      break;
    }
    current = current.cause;
  }
  let detail = parts.join(": ").replace(/([?&]key=)[^&\s]*/gi, "$1[redacted]");
  if (typeof apiKey === "string" && apiKey) {
    detail = detail.replaceAll(apiKey, "[redacted]");
  }
  return detail.slice(0, 300);
}

function providerLabel(provider) {
  return {
    google: "Google Gemini",
    ollama: "Ollama",
    "openai-compatible": "OpenAI-compatible provider",
  }[provider] || "AI provider";
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await runCli();
}
