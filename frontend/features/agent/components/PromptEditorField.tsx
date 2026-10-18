"use client";

import { Input, Segmented, Typography } from "antd";
import { useMemo, useState } from "react";
import { estimatePromptTokens, promptCharCount } from "@/domain/agent/promptPreview";
import { renderContentHtml } from "@/domain/render/markdown";

type PromptEditorFieldProps = {
  value?: string;
  onChange?: (value: string) => void;
  placeholder?: string;
};

/** Controlled prompt input with edit / preview / split modes and size hints. */
export function PromptEditorField({ value, onChange, placeholder }: PromptEditorFieldProps) {
  const [mode, setMode] = useState<"edit" | "preview" | "split">("split");
  const text = value ?? "";
  const preview = useMemo(() => renderContentHtml(text, "markdown"), [text]);
  const showEditor = mode !== "preview";
  const showPreview = mode !== "edit";

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 pb-2">
        <Typography.Text type="secondary" className="text-xs">
          {promptCharCount(text)} 字符 · 约 {estimatePromptTokens(text).toLocaleString()} tokens
        </Typography.Text>
        <Segmented
          size="small"
          value={mode}
          onChange={(next) => setMode(next as "edit" | "preview" | "split")}
          options={[{ label: "编辑", value: "edit" }, { label: "预览", value: "preview" }, { label: "并排", value: "split" }]}
        />
      </div>
      <div className={mode === "split" ? "grid grid-cols-1 gap-2 md:grid-cols-2" : undefined}>
        {showEditor ? (
          <Input.TextArea
            value={text}
            onChange={(event) => onChange?.(event.target.value)}
            placeholder={placeholder}
            autoSize={{ minRows: 12, maxRows: 22 }}
            className="font-mono text-xs"
          />
        ) : null}
        {showPreview ? (
          <div
            aria-label="提示词预览"
            className="max-h-[24rem] min-h-[12rem] overflow-auto rounded border border-slate-200 bg-slate-50 px-3 py-2 text-sm"
            dangerouslySetInnerHTML={preview}
          />
        ) : null}
      </div>
    </div>
  );
}
