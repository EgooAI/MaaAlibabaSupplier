import type { ComponentProps } from "react";

export function HydrationSafeTable(props: ComponentProps<"table">) {
  return <table {...props} suppressHydrationWarning />;
}
