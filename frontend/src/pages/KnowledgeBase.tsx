import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { DataTable, type Column } from "@/components/DataTable";
import { KbArticleFormDialog } from "@/components/KbArticleFormDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { dash } from "@/lib/format";
import { KB_CATEGORY_LABEL } from "@/lib/kbCategory";
import type { KbArticle, KbArticlesResponse } from "@/lib/types";

const columns: Column<KbArticle>[] = [
  {
    key: "category",
    header: "Categoría",
    render: (a) => <Badge variant="neutral">{KB_CATEGORY_LABEL[a.category] ?? a.category}</Badge>,
    sortValue: (a) => a.category,
  },
  { key: "title", header: "Título", render: (a) => dash(a.title) },
  {
    key: "error_pattern",
    header: "Patrón",
    sortable: false,
    render: (a) => <code className="text-xs">{a.error_pattern}</code>,
  },
  { key: "updated_by", header: "Actualizado por", render: (a) => dash(a.updated_by) },
];

/** Base de conocimiento de errores — espacio de nav propio ("Base de
 *  conocimiento", junto a Identity), primera página de PAM Web Console **sin**
 *  selector de tenant (el conocimiento es genérico de CyberArk, no de un
 *  cliente). Click en una fila navega a `/knowledge-base/:id`
 *  (KnowledgeBaseArticle.tsx) — sección propia, no un panel compartiendo
 *  pantalla con la lista. Ver docs/specs/done/knowledge-base.md. */
export default function KnowledgeBase() {
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const q = useQuery({
    queryKey: ["kb-articles"],
    queryFn: () => api<KbArticlesResponse>("/knowledge-base"),
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-[22px] font-bold text-navy-900">Base de conocimiento</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Causas y soluciones conocidas para errores de CyberArk — compartida entre todos los
            tenants.
          </p>
        </div>
        <Button size="sm" onClick={() => setCreating(true)}>
          <Plus className="size-3.5" />
          Nuevo artículo
        </Button>
      </div>

      {q.isLoading && <p className="text-sm text-muted-foreground">Cargando…</p>}
      {q.isError && <p className="text-sm text-destructive">{(q.error as Error).message}</p>}
      {q.data && q.data.articles.length === 0 && (
        <p className="text-sm text-muted-foreground">
          Sin artículos todavía — se pueden cargar acá o directamente desde Troubleshooting, al
          resolver un error.
        </p>
      )}
      {q.data && q.data.articles.length > 0 && (
        <DataTable
          rows={q.data.articles}
          columns={columns}
          tableId="kb-articles"
          getRowKey={(a) => a.id}
          minWidth={800}
          onRowClick={(a) => navigate(`/knowledge-base/${a.id}`)}
          filterPlaceholder="buscar por título / patrón"
        />
      )}

      <KbArticleFormDialog
        open={creating}
        onClose={() => setCreating(false)}
        onDone={() => setCreating(false)}
      />
    </div>
  );
}
