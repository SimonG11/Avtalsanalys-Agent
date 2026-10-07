"use client";
/**
 * What: the person's own files in the conversation: those attached in the chat field, waiting
 * to go with the next question, and those already sent with a question.
 *
 * Why: the agent can compare the person's contract with the framework agreements
 * (webbapp-kontrakt.md, points 33-38). A file is uploaded as soon as it is attached, so the
 * agent can read it when the question comes, and it is shown as a chip on the question it was
 * sent with. The agent's steps and the sources name a file by its name, not its id.
 *
 * How: the files belong to the conversation's AG-UI thread, so every request names
 * `agent.threadId`, the id the agent's runs use too. The requests go to the web app's
 * /api/uploads routes, which forward them to the API. The chips on sent questions live only in
 * the browser, like the answers; the agent learns about the thread's files from the backend.
 */
import { randomUUID, useAgent } from "@copilotkit/react-core/v2";
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { AGENT_ID } from "@/lib/agent";
import { UploadSchema, checkFile, uploadErrorMessage } from "@/lib/uploads";
import type { Upload } from "@/lib/uploads";

export interface AttachedFile {
  /** The chip's own id in the browser. */
  key: string;
  name: string;
  status: "uploading" | "ready" | "failed";
  /** What the API said about the file, once it is uploaded. */
  upload?: Upload;
  /** Why the file could not be uploaded, in Swedish. */
  error?: string;
}

interface UploadsValue {
  threadId: string;
  /** The files attached in the chat field, for the next question. */
  attached: readonly AttachedFile[];
  add: (files: Iterable<File>) => void;
  remove: (key: string) => void;
  /** Moves the uploaded files to the question they are sent with, and clears the field's. */
  commit: (messageId: string) => void;
  filesOf: (messageId: string) => readonly AttachedFile[];
  /** The names of the uploaded files by their ids. */
  names: ReadonlyMap<string, string>;
}

const NO_FILES: readonly AttachedFile[] = [];

const UploadsContext = createContext<UploadsValue>({
  threadId: "",
  attached: NO_FILES,
  add: () => {},
  remove: () => {},
  commit: () => {},
  filesOf: () => NO_FILES,
  names: new Map(),
});

export function useUploads(): UploadsValue {
  return useContext(UploadsContext);
}

export function UploadsProvider({ children }: { children: ReactNode }) {
  const { agent } = useAgent({ agentId: AGENT_ID, updates: [] });
  const threadId = agent.threadId;
  const [attached, setAttached] = useState<AttachedFile[]>([]);
  const [sent, setSent] = useState<ReadonlyMap<string, readonly AttachedFile[]>>(new Map());
  const requests = useRef(new Map<string, AbortController>());

  const update = useCallback((key: string, change: Partial<AttachedFile>) => {
    setAttached((files) => {
      const next = files.map((file) => (file.key === key ? { ...file, ...change } : file));
      // Uploading the same file again gives the same upload; one chip is enough.
      const id = change.upload?.upload_id;
      const first = next.findIndex((file) => file.upload?.upload_id === id);
      return id === undefined
        ? next
        : next.filter((file, index) => file.key !== key || index === first);
    });
  }, []);

  const upload = useCallback(
    async (key: string, file: File) => {
      const controller = new AbortController();
      requests.current.set(key, controller);
      const form = new FormData();
      form.append("file", file);
      form.append("thread_id", threadId);
      try {
        const response = await fetch("/api/uploads", {
          method: "POST",
          body: form,
          signal: controller.signal,
        });
        const body: unknown = await response.json().catch(() => null);
        if (!response.ok) {
          update(key, { status: "failed", error: uploadErrorMessage(response.status, body) });
          return;
        }
        const parsed = UploadSchema.safeParse(body);
        update(
          key,
          parsed.success
            ? { status: "ready", upload: parsed.data }
            : { status: "failed", error: "Svaret om filen gick inte att läsa." },
        );
      } catch (error) {
        if (controller.signal.aborted) return;
        console.error("The file could not be uploaded", error);
        update(key, { status: "failed", error: "Filen kunde inte laddas upp." });
      } finally {
        requests.current.delete(key);
      }
    },
    [threadId, update],
  );

  const add = useCallback(
    (files: Iterable<File>) => {
      const added: AttachedFile[] = [];
      for (const file of files) {
        const key = randomUUID();
        const error = checkFile(file);
        added.push({
          key,
          name: file.name,
          status: error ? "failed" : "uploading",
          error: error ?? undefined,
        });
        if (!error) void upload(key, file);
      }
      setAttached((current) => [...current, ...added]);
    },
    [upload],
  );

  const remove = useCallback(
    (key: string) => {
      requests.current.get(key)?.abort();
      const id = attached.find((file) => file.key === key)?.upload?.upload_id;
      if (id) {
        const url = `/api/uploads/${encodeURIComponent(id)}?thread_id=${encodeURIComponent(threadId)}`;
        fetch(url, { method: "DELETE" }).catch((error: unknown) =>
          console.error("The file could not be removed", error),
        );
      }
      setAttached((files) => files.filter((file) => file.key !== key));
    },
    [attached, threadId],
  );

  const commit = useCallback(
    (messageId: string) => {
      const ready = attached.filter((file) => file.status === "ready");
      if (ready.length > 0) setSent((current) => new Map(current).set(messageId, ready));
      setAttached([]);
    },
    [attached],
  );

  const filesOf = useCallback((messageId: string) => sent.get(messageId) ?? NO_FILES, [sent]);

  const names = useMemo(() => {
    const map = new Map<string, string>();
    for (const file of [...[...sent.values()].flat(), ...attached]) {
      if (file.upload) map.set(file.upload.upload_id, file.upload.filename);
    }
    return map;
  }, [sent, attached]);

  const value = useMemo(
    () => ({ threadId, attached, add, remove, commit, filesOf, names }),
    [threadId, attached, add, remove, commit, filesOf, names],
  );
  return <UploadsContext.Provider value={value}>{children}</UploadsContext.Provider>;
}
