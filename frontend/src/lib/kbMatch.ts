import type { KbArticle } from "@/lib/types";

/** Mismo criterio que `match_article` del backend (texto simple, sin
 *  mayús/minús, no regex) — se usa acá para no pagar un endpoint de
 *  "buscar por texto": la lista completa de artículos ya se trae una
 *  sola vez. Ver docs/specs/done/knowledge-base.md. */
export function matchKbArticle(
  text: string | undefined,
  articles: KbArticle[],
): KbArticle | undefined {
  const haystack = (text ?? "").trim().toLowerCase();
  if (!haystack) return undefined;
  return articles.find((a) => {
    const pattern = (a.error_pattern ?? "").trim().toLowerCase();
    return pattern.length > 0 && haystack.includes(pattern);
  });
}
