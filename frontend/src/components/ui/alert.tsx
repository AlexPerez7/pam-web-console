import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const alertVariants = cva("rounded-md border px-4 py-3 text-sm", {
  variants: {
    variant: {
      default: "border-border bg-card text-card-foreground",
      destructive: "border-line-cyberdefense/30 bg-[#fbe2e2] text-danger-ink",
      warning: "border-line-consulting/30 bg-[#fdf0d6] text-warning-ink",
      success: "border-line-identity/30 bg-[#eaf4de] text-success-ink",
      info: "border-sky-300 bg-sky-50 text-navy-900",
    },
  },
  defaultVariants: { variant: "default" },
});

export interface AlertProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof alertVariants> {}

function Alert({ className, variant, ...props }: AlertProps) {
  return (
    <div role="alert" className={cn(alertVariants({ variant }), className)} {...props} />
  );
}

export { Alert };
