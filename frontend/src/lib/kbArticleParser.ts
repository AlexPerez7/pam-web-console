/** Heurística para precargar el formulario de la base de conocimiento a
 *  partir del texto completo de un artículo de CyberArk pegado tal cual
 *  (ej. copiado de community.cyberark.com) — sin IA, solo reconoce los
 *  encabezados de sección fijos que usa ese formato ("Title", "Cause",
 *  "Resolution", etc.) y agrupa el texto entre ellos. No garantiza
 *  precisión — el operador siempre revisa/edita antes de guardar. Ver
 *  docs/specs/done/knowledge-base.md. */

type SectionKey = "title" | "cause" | "resolution";

const SECTION_HEADERS: Record<string, SectionKey> = {
  title: "title",
  cause: "cause",
  resolution: "resolution",
};

// Encabezados que también cortan una sección en curso, aunque no nos
// interese su contenido (si no están acá, su texto se pegaría a la
// sección anterior por error).
const IGNORED_HEADERS = new Set([
  "issue / details",
  "issue/details",
  "issue",
  "product",
  "environment",
  "resources",
  "resources:",
]);

export interface ParsedKbArticle {
  title?: string;
  errorPattern?: string;
  cause?: string;
  solution?: string;
}

export function parseCyberArkArticle(raw: string): ParsedKbArticle {
  const lines = raw.split(/\r?\n/);
  const sections: Partial<Record<SectionKey, string[]>> = {};
  let current: SectionKey | null = null;

  for (const line of lines) {
    const normalized = line.trim().toLowerCase().replace(/:$/, "");
    if (normalized in SECTION_HEADERS) {
      current = SECTION_HEADERS[normalized];
      sections[current] ??= [];
      continue;
    }
    if (IGNORED_HEADERS.has(normalized)) {
      current = null;
      continue;
    }
    if (current) {
      sections[current]!.push(line);
    }
  }

  const join = (key: SectionKey): string | undefined => {
    const text = sections[key]
      ?.join("\n")
      .trim()
      .replace(/\n{3,}/g, "\n\n");
    return text || undefined;
  };

  const title = join("title");
  const cause = join("cause");
  const solution = join("resolution");

  let errorPattern: string | undefined;
  if (title) {
    const match = title.match(/'([^']+)'/);
    if (match) errorPattern = match[1].replace(/\.$/, "").trim() || undefined;
  }

  return { title, errorPattern, cause, solution };
}
