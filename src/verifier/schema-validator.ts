import AjvModule from "ajv";
import addFormatsModule from "ajv-formats";
import type { ErrorObject } from "ajv";
import { readFile } from "node:fs/promises";
import path from "node:path";

const SCHEMA_DIR = path.resolve(process.cwd(), "schemas");

const Ajv = AjvModule.default;
const addFormats = addFormatsModule.default;

const ajv = new Ajv({ allErrors: true, strict: false });
addFormats(ajv);

const schemaCache = new Map<string, object>();

export async function loadSchema(name: string): Promise<object> {
  if (schemaCache.has(name)) {
    return schemaCache.get(name)!;
  }
  const filePath = path.join(SCHEMA_DIR, name);
  const raw = await readFile(filePath, "utf8");
  const schema = JSON.parse(raw) as object;
  schemaCache.set(name, schema);
  return schema;
}

export async function validateAgainstSchema(
  schemaName: string,
  data: unknown
): Promise<{ valid: boolean; errors?: string[] }> {
  const schema = await loadSchema(schemaName);
  const validate = ajv.compile(schema);
  const valid = validate(data);
  if (valid) {
    return { valid: true };
  }
  const errors = (validate.errors ?? []).map(
    (e: ErrorObject) => `${e.instancePath || "/"} ${e.message ?? "invalid"}`
  );
  return { valid: false, errors };
}

export async function preloadSchemas(): Promise<void> {
  const names = [
    "trip-meta.schema.json",
    "session.schema.json",
    "poi-list.schema.json",
    "itinerary.schema.json",
    "verifier-error.schema.json",
  ];
  await Promise.all(names.map((n) => loadSchema(n)));
}
