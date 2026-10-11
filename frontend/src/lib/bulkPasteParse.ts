/** Parsea el formato en que suelen llegar los tickets de alta de cuentas:
 *  bloques de `Etiqueta: valor` (uno por cuenta), separados por línea en
 *  blanco o por la repetición de la etiqueta "Address" — NO es CSV/TSV, es
 *  texto pegado tal cual del cuerpo del ticket. La plataforma casi nunca
 *  viene en el ticket (el cliente no la conoce) — se completa aparte, una
 *  sola vez para todo el lote, no acá. Ver `AccountBulkCreateDialog.tsx`. */

export interface ParsedPasteRow {
  address: string;
  userName: string;
  safeName: string;
  secret: string;
  /** "Log On To" (propiedad `LogonDomain`). En las cuentas locales de
   *  Windows lleva el mismo host que `address`; el ticket casi nunca lo
   *  trae, así que el diálogo lo completa con un atajo. */
  logonTo: string;
  /** "Remote machine access": los hosts a los que puede conectarse una
   *  cuenta de dominio. Texto libre; al armar el CSV se normaliza a la
   *  lista separada por `;` que espera CyberArk. */
  remoteMachines: string;
  /** El ticket casi nunca la trae — se completa aparte en el diálogo
   *  (por defecto para todas las filas, con override fila por fila para
   *  el caso real de un lote con plataformas mixtas). */
  platformId: string;
}

export interface ParsedPasteResult {
  rows: ParsedPasteRow[];
  /** Líneas que no pudieron asociarse a ningún bloque reconocible. */
  unmatchedLines: string[];
}

const LABELS: Record<string, keyof ParsedPasteRow> = {
  address: "address",
  ip: "address",
  host: "address",
  target: "address",
  user: "userName",
  username: "userName",
  usuario: "userName",
  pass: "secret",
  password: "secret",
  contraseña: "secret",
  contrasena: "secret",
  clave: "secret",
  safe: "safeName",
  cajafuerte: "safeName",
  logonto: "logonTo",
  logon: "logonTo",
  logondomain: "logonTo",
  remotemachines: "remoteMachines",
  remotemachine: "remoteMachines",
  remotemachineaccess: "remoteMachines",
  maquinas: "remoteMachines",
  maquinasremotas: "remoteMachines",
};

function normalizeLabel(raw: string): keyof ParsedPasteRow | null {
  const key = raw.trim().toLowerCase().replace(/\s+/g, "");
  return LABELS[key] ?? null;
}

/** Address mencionado una sola vez en texto libre, sin `Address:` por
 *  bloque (ej. "Favor agregar el address ARMZACVMAGNT01 a las siguientes
 *  cuentas", o "agregando el siguiente target GRGSAPIBMLMT a las
 *  siguientes cuentas"). Acepta address/target/host/servidor/equipo/máquina
 *  y exige espacio —no `:`— tras la palabra, para no confundirse con la
 *  etiqueta `Address:` de un bloque. */
const FREE_ADDRESS_RE =
  /(?:^|[^\p{L}])(?:address|target|host|servidor|equipo|m[aá]quina)\s+([^\s:,;]+)/iu;

function freeTextAddress(line: string): string | null {
  const m = line.match(FREE_ADDRESS_RE);
  return m ? m[1] : null;
}

export function parseBulkPasteBlocks(text: string): ParsedPasteResult {
  const rows: ParsedPasteRow[] = [];
  const unmatchedLines: string[] = [];
  let current: Partial<ParsedPasteRow> = {};
  let sharedAddress = "";

  function flush() {
    if (current.address || current.userName || current.safeName || current.secret) {
      rows.push({
        address: current.address ?? "",
        userName: current.userName ?? "",
        safeName: current.safeName ?? "",
        secret: current.secret ?? "",
        logonTo: current.logonTo ?? "",
        remoteMachines: current.remoteMachines ?? "",
        platformId: "",
      });
    }
    current = {};
  }

  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) {
      flush();
      continue;
    }
    const m = line.match(/^([^:]+):\s*(.*)$/);
    const field = m ? normalizeLabel(m[1]) : null;
    if (!m || !field) {
      const free = freeTextAddress(line);
      if (free && !sharedAddress) sharedAddress = free;
      else unmatchedLines.push(line);
      continue;
    }
    // "Address:" repetido sin línea en blanco antes -> nuevo bloque.
    if (field === "address" && current.address) flush();
    current[field] = m[2].trim();
  }
  flush();

  // Como el safe por defecto: sólo completa filas vacías, nunca pisa un
  // address ya parseado de su propio bloque.
  if (sharedAddress) {
    for (const r of rows) if (!r.address) r.address = sharedAddress;
  }

  return { rows, unmatchedLines };
}
