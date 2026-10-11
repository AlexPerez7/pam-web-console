import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink, Loader2, Pencil, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { KB_CATEGORY_LABEL } from "@/lib/kbCategory";
import type { KbArticle } from "@/lib/types";
import { toast } from "@/components/ui/toast";

/** Un artículo completo, en su propia ruta (`/knowledge-base/:id`) — a
 *  diferencia de Safes/Campañas, acá **no** es un panel compartiendo
 *  pantalla con la lista: es una sección propia, con "Editar" que vuelve
 *  editables los mismos campos en el lugar (sin diálogo aparte). Ver
 *  docs/specs/done/knowledge-base.md. */
export default function KnowledgeBaseArticle() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const [category, setCategory] = useState("rotation");
  const [title, setTitle] = useState("");
  const [pattern, setPattern] = useState("");
  const [cause, setCause] = useState("");
  const [solution, setSolution] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");

  const q = useQuery({
    queryKey: ["kb-article", id],
    queryFn: () => api<KbArticle>(`/knowledge-base/${id}`),
    enabled: !!id,
  });

  useEffect(() => {
    if (!q.data) return;
    setCategory(q.data.category);
    setTitle(q.data.title);
    setPattern(q.data.error_pattern);
    setCause(q.data.cause);
    setSolution(q.data.solution);
    setSourceUrl(q.data.source_url ?? "");
  }, [q.data]);

  const save = useMutation({
    mutationFn: () =>
      api<KbArticle>(`/knowledge-base/${id}`, {
        method: "PUT",
        body: {
          category,
          title,
          error_pattern: pattern,
          cause,
          solution,
          source_url: sourceUrl.trim() || undefined,
        },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kb-article", id] });
      qc.invalidateQueries({ queryKey: ["kb-articles"] });
      setEditing(false);
      toast({ title: "Artículo guardado" });
    },
  });

  const del = useMutation({
    mutationFn: () => api(`/knowledge-base/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kb-articles"] });
      toast({ title: "Artículo borrado" });
      navigate("/knowledge-base");
    },
  });

  function cancelEdit() {
    if (!q.data) return;
    setCategory(q.data.category);
    setTitle(q.data.title);
    setPattern(q.data.error_pattern);
    setCause(q.data.cause);
    setSolution(q.data.solution);
    setSourceUrl(q.data.source_url ?? "");
    setEditing(false);
  }

  const valid = title.trim() && pattern.trim() && cause.trim() && solution.trim();

  return (
    <div className="flex flex-col gap-4">
      <Link
        to="/knowledge-base"
        className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        Volver a la base de conocimiento
      </Link>

      {q.isLoading && (
        <div className="flex justify-center py-10">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      )}
      {q.isError && <Alert variant="destructive">{(q.error as Error).message}</Alert>}

      {q.data && (
        <div className="flex flex-col gap-5 rounded-lg border border-border bg-card p-6">
          <div className="flex flex-col gap-3">
            {editing ? (
              <div className="flex flex-wrap items-center gap-2">
                <Select value={category} onValueChange={setCategory}>
                  <SelectTrigger aria-label="Categoría" className="h-8 w-44 text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(KB_CATEGORY_LABEL).map(([k, label]) => (
                      <SelectItem key={k} value={k}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Input
                  className="h-8 w-64 font-mono text-xs"
                  value={pattern}
                  onChange={(e) => setPattern(e.target.value)}
                  placeholder="Patrón de error"
                />
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="info">{KB_CATEGORY_LABEL[q.data.category] ?? q.data.category}</Badge>
                <code className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                  {q.data.error_pattern}
                </code>
              </div>
            )}

            {editing ? (
              <Input
                className="h-auto border-none px-0 text-[22px] font-bold text-navy-900 shadow-none focus-visible:ring-1"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Título del artículo"
              />
            ) : (
              <h1 className="text-[22px] font-bold text-navy-900">{q.data.title}</h1>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2 border-y border-border py-3">
            {!editing ? (
              <>
                <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                  <Pencil className="size-3.5" />
                  Editar
                </Button>
                {q.data.source_url && (
                  <Button variant="outline" size="sm" asChild>
                    <a href={q.data.source_url} target="_blank" rel="noreferrer">
                      <ExternalLink className="size-3.5" />
                      Ver artículo original
                    </a>
                  </Button>
                )}
                <div className="ml-auto">
                  {!confirmingDelete ? (
                    <Button variant="ghost" size="sm" onClick={() => setConfirmingDelete(true)}>
                      <Trash2 className="size-3.5" />
                      Borrar
                    </Button>
                  ) : (
                    <div className="flex items-center gap-1">
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={del.isPending}
                        onClick={() => del.mutate()}
                      >
                        Confirmar borrado
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => setConfirmingDelete(false)}>
                        Cancelar
                      </Button>
                    </div>
                  )}
                </div>
              </>
            ) : (
              <>
                <label className="flex-1 text-xs text-muted-foreground">
                  Link externo (opcional)
                  <Input
                    className="mt-1 h-8 text-sm"
                    value={sourceUrl}
                    onChange={(e) => setSourceUrl(e.target.value)}
                    placeholder="https://community.cyberark.com/..."
                  />
                </label>
                <div className="ml-auto flex items-center gap-2 self-end">
                  <Button variant="ghost" size="sm" onClick={cancelEdit}>
                    Cancelar
                  </Button>
                  <Button
                    size="sm"
                    disabled={!valid || save.isPending}
                    onClick={() => save.mutate()}
                  >
                    {save.isPending && <Loader2 className="size-3.5 animate-spin" />}
                    Guardar
                  </Button>
                </div>
              </>
            )}
          </div>

          {save.isError && <Alert variant="destructive">{(save.error as Error).message}</Alert>}
          {del.isError && <Alert variant="destructive">{(del.error as Error).message}</Alert>}

          <div className="flex flex-col gap-5">
            <div>
              <h2 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Causa posible
              </h2>
              {editing ? (
                <textarea
                  value={cause}
                  onChange={(e) => setCause(e.target.value)}
                  rows={5}
                  className="w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm leading-relaxed"
                />
              ) : (
                <p className="whitespace-pre-wrap text-sm leading-relaxed">{q.data.cause}</p>
              )}
            </div>
            <div>
              <h2 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Solución
              </h2>
              {editing ? (
                <textarea
                  value={solution}
                  onChange={(e) => setSolution(e.target.value)}
                  rows={8}
                  className="w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm leading-relaxed"
                />
              ) : (
                <p className="whitespace-pre-wrap text-sm leading-relaxed">{q.data.solution}</p>
              )}
            </div>
          </div>

          <p className="text-xs text-muted-foreground">
            Actualizado por {q.data.updated_by} · {fmtDateTime(q.data.updated_at)}
          </p>
        </div>
      )}
    </div>
  );
}
