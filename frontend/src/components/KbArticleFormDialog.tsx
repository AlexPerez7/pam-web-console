import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { parseCyberArkArticle } from "@/lib/kbArticleParser";
import { KB_CATEGORY_LABEL } from "@/lib/kbCategory";
import { toast } from "@/components/ui/toast";

/** Alta de un artículo de la base de conocimiento. `initialPattern`
 *  precarga el patrón cuando se abre desde "Agregar a la base de
 *  conocimiento" en Troubleshooting, con el `Reason` real del error —
 *  editar un artículo existente vive en `KnowledgeBaseArticle.tsx` (en
 *  línea, en la página del artículo), no acá. Ver
 *  docs/specs/done/knowledge-base.md. */
export function KbArticleFormDialog({
  open,
  onClose,
  initialPattern,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  initialPattern?: string;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const [category, setCategory] = useState("rotation");
  const [title, setTitle] = useState("");
  const [pattern, setPattern] = useState("");
  const [cause, setCause] = useState("");
  const [solution, setSolution] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [pasteText, setPasteText] = useState("");
  const [parseMsg, setParseMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setCategory("rotation");
    setTitle("");
    setPattern(initialPattern ?? "");
    setCause("");
    setSolution("");
    setSourceUrl("");
    setPasteText("");
    setParseMsg(null);
  }, [open, initialPattern]);

  function handleParse() {
    const parsed = parseCyberArkArticle(pasteText);
    let filled = 0;
    if (parsed.title && !title.trim()) {
      setTitle(parsed.title);
      filled++;
    }
    if (parsed.errorPattern && !pattern.trim()) {
      setPattern(parsed.errorPattern);
      filled++;
    }
    if (parsed.cause && !cause.trim()) {
      setCause(parsed.cause);
      filled++;
    }
    if (parsed.solution && !solution.trim()) {
      setSolution(parsed.solution);
      filled++;
    }
    setParseMsg(
      filled > 0
        ? `Se completaron ${filled} campo(s) — revise el contenido antes de guardar.`
        : "No se reconoció ningún campo en el texto pegado — cárguelos a mano.",
    );
  }

  const save = useMutation({
    mutationFn: () =>
      api("/knowledge-base", {
        method: "POST",
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
      qc.invalidateQueries({ queryKey: ["kb-articles"] });
      toast({ title: "Artículo agregado a la base de conocimiento", description: title });
      onDone();
    },
  });

  const valid = title.trim() && pattern.trim() && cause.trim() && solution.trim();

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogTitle>Nuevo artículo</DialogTitle>
        <div className="flex flex-col gap-3">
          <div className="rounded-md border border-dashed border-input bg-muted/30 p-2.5">
            <label className="text-xs text-muted-foreground">
              Pegar artículo (opcional) — pegue el texto completo de un artículo de
              CyberArk para completar los campos vacíos automáticamente
              <textarea
                value={pasteText}
                onChange={(e) => setPasteText(e.target.value)}
                rows={3}
                placeholder="Pegue aquí el contenido de community.cyberark.com…"
                className="mt-1 w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
              />
            </label>
            <div className="mt-1.5 flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!pasteText.trim()}
                onClick={handleParse}
              >
                <Sparkles className="size-3.5" />
                Detectar campos
              </Button>
              {parseMsg && <span className="text-xs text-muted-foreground">{parseMsg}</span>}
            </div>
          </div>
          <label className="text-xs text-muted-foreground">
            Categoría
            <Select value={category} onValueChange={setCategory}>
              <SelectTrigger className="mt-1 h-8 text-sm">
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
          </label>
          <label className="text-xs text-muted-foreground">
            Título
            <Input
              className="mt-1 h-8 text-sm"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="ej. CPM-winRc-5 Access is denied"
            />
          </label>
          <label className="text-xs text-muted-foreground">
            Patrón de error (texto a buscar en el motivo real, sin mayús/minús)
            <Input
              className="mt-1 h-8 text-sm"
              value={pattern}
              onChange={(e) => setPattern(e.target.value)}
              placeholder="ej. Access is denied"
            />
          </label>
          <label className="text-xs text-muted-foreground">
            Causa posible
            <textarea
              value={cause}
              onChange={(e) => setCause(e.target.value)}
              rows={3}
              className="mt-1 w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
            />
          </label>
          <label className="text-xs text-muted-foreground">
            Solución
            <textarea
              value={solution}
              onChange={(e) => setSolution(e.target.value)}
              rows={6}
              className="mt-1 w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
            />
          </label>
          <label className="text-xs text-muted-foreground">
            Link externo (opcional)
            <Input
              className="mt-1 h-8 text-sm"
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              placeholder="https://community.cyberark.com/..."
            />
          </label>
          {save.isError && <Alert variant="destructive">{(save.error as Error).message}</Alert>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button disabled={!valid || save.isPending} onClick={() => save.mutate()}>
            {save.isPending && <Loader2 className="size-3.5 animate-spin" />}
            Crear
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
