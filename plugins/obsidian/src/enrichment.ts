export class EnrichmentBoundaryError extends Error {
	constructor(message: string) {
		super(message);
		this.name = "EnrichmentBoundaryError";
	}
}

export interface EnrichmentFields {
	summary?: string;
	examples?: string[];
	connections?: string[];
	sources?: string[];
}

const OWNED_FIELDS = new Set(["summary", "examples", "connections", "sources"]);

function enrichmentObject(value: unknown, label: string): Record<string, unknown> {
	if (typeof value !== "object" || value === null || Array.isArray(value)) {
		throw new EnrichmentBoundaryError(`${label} must be an object`);
	}
	return value as Record<string, unknown>;
}

function normalizeField(key: string, value: unknown): string | string[] {
	if (key === "summary") {
		if (typeof value !== "string" || !value.trim()) {
			throw new EnrichmentBoundaryError("enrichment summary must be non-empty text");
		}
		return value;
	}
	if (
		!Array.isArray(value) ||
		value.some((entry) => typeof entry !== "string" || !entry.trim())
	) {
		throw new EnrichmentBoundaryError(`enrichment ${key} must contain non-empty text`);
	}
	return [...value];
}

/**
 * Return an additive copy. Schedule, evidence, hashes, and learner fields stay
 * outside the only accepted patch namespace and retain their original values.
 */
export function applyEnrichmentAdditions<T extends Record<string, unknown>>(
	before: T,
	additions: unknown,
): T & { enrichment: EnrichmentFields } {
	const patch = enrichmentObject(additions, "enrichment additions");
	const existingValue = before.enrichment ?? {};
	const existing = enrichmentObject(existingValue, "existing enrichment");
	const next: Record<string, string | string[]> = {};

	for (const [key, value] of Object.entries(existing)) {
		if (!OWNED_FIELDS.has(key)) {
			throw new EnrichmentBoundaryError(`existing enrichment field is not owned: ${key}`);
		}
		next[key] = normalizeField(key, value);
	}
	for (const [key, value] of Object.entries(patch)) {
		if (!OWNED_FIELDS.has(key)) {
			throw new EnrichmentBoundaryError(
				`field is outside the additive enrichment boundary: ${key}`,
			);
		}
		if (Object.prototype.hasOwnProperty.call(existing, key)) {
			throw new EnrichmentBoundaryError(
				`enrichment is additive; existing field cannot be replaced: ${key}`,
			);
		}
		next[key] = normalizeField(key, value);
	}

	return { ...before, enrichment: next } as T & { enrichment: EnrichmentFields };
}
