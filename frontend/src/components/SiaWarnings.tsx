import { Alert } from "@/components/ui/alert";

/** Partes de una página de SIA que no se pudieron leer (p. ej. secretos de
 *  VM caídos mientras los de DB sí llegaron). El resto se muestra igual. */
export function SiaWarnings({ warnings }: { warnings?: string[] }) {
  if (!warnings?.length) return null;
  return (
    <Alert variant="warning" className="mb-3">
      <p className="font-medium">No se pudo leer todo:</p>
      <ul className="mt-1 list-disc pl-5">
        {warnings.map((w) => (
          <li key={w}>{w}</li>
        ))}
      </ul>
    </Alert>
  );
}
