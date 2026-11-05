"use client";

import { TranslationOutlined, UndoOutlined } from "@ant-design/icons";
import { Button, Input } from "antd";
import { useEffect, useRef, useState } from "react";
import { AccountChangedError, useAccountBackend } from "@/features/account/AccountProvider";
import { backendErrorMessage } from "@/services/errors";

type Applied = { previous: string; translation: string };

/** Translates Chinese typed below the composer into the buyer's language and replaces the draft. */
export function ComposeTranslator({ conversationId, draft, onDraftChange }: { conversationId: string; draft: string; onDraftChange: (value: string) => void }) {
  const backend = useAccountBackend();
  const [source, setSource] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [applied, setApplied] = useState<Applied>();
  const alive = useRef(true);
  const draftRef = useRef(draft);
  useEffect(() => { draftRef.current = draft; }, [draft]);
  // A result that arrives after the conversation changed must not touch the new draft.
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  async function translate() {
    const text = source.trim();
    if (!text || loading) return;
    setLoading(true);
    setError(undefined);
    try {
      const { translation } = await backend.composeTranslation(conversationId, text);
      if (!alive.current) return;
      // Repeated translations keep undoing back to the draft that existed before the first one.
      setApplied((current) => ({ previous: current && draftRef.current === current.translation ? current.previous : draftRef.current, translation }));
      onDraftChange(translation);
    } catch (reason) {
      if (alive.current && !(reason instanceof AccountChangedError)) setError(backendErrorMessage(reason, "翻译失败，请重试"));
    } finally {
      if (alive.current) setLoading(false);
    }
  }

  return <div className="mt-2">
    <div className="flex items-end gap-2">
      <Input.TextArea
        aria-label="需要翻译的文字"
        autoSize={{ minRows: 1, maxRows: 4 }}
        maxLength={4000}
        value={source}
        onChange={(event) => setSource(event.target.value)}
        placeholder="输入需要翻译的中文，译文会替换上方输入框"
      />
      <Button icon={<TranslationOutlined />} loading={loading} disabled={!source.trim()} onClick={() => void translate()}>翻译</Button>
    </div>
    {error ? <div role="alert" className="mt-1 text-xs text-red-600">{error}</div> : null}
    {applied && draft === applied.translation ? <div className="mt-1 text-xs text-slate-500">
      已替换上方输入框
      <Button type="link" size="small" icon={<UndoOutlined />} onClick={() => { onDraftChange(applied.previous); setApplied(undefined); }}>撤销</Button>
    </div> : null}
  </div>;
}
