/**
 * What: a small chip for one of the person's files: its name, and a line on how it went (being
 * uploaded, its pages, or why it could not be uploaded), with a button to remove it while it
 * is still in the chat field.
 *
 * Why: the person should see which files go with a question, and that they arrived, before
 * the agent is asked about them.
 *
 * How: a presentational component over an AttachedFile (Uploads.tsx); the chat field passes
 * `onRemove`, a sent question does not.
 */
import { describeUpload } from "@/lib/uploads";

import { Icon, Spinner } from "./icons";
import type { AttachedFile } from "./Uploads";
import styles from "./FileChip.module.css";

export function FileChip({ file, onRemove }: { file: AttachedFile; onRemove?: () => void }) {
  const failed = file.status === "failed";
  const line =
    file.status === "uploading"
      ? "Laddar upp …"
      : failed
        ? (file.error ?? "Filen kunde inte laddas upp.")
        : file.upload
          ? describeUpload(file.upload)
          : null;
  return (
    <div
      className={failed ? `${styles.chip} ${styles.failed}` : styles.chip}
      data-testid="file-chip"
      data-status={file.status}
    >
      <span className={styles.icon}>
        {file.status === "uploading" ? (
          <Spinner size={16} />
        ) : (
          <Icon name={failed ? "alert" : "document"} size={16} />
        )}
      </span>
      <span className={styles.text}>
        <span className={styles.name} title={file.name}>
          {file.name}
        </span>
        {line && (
          <span className={styles.line} role={failed ? "alert" : undefined}>
            {line}
          </span>
        )}
      </span>
      {onRemove && (
        <button
          type="button"
          className={styles.remove}
          onClick={onRemove}
          aria-label={`Ta bort ${file.name}`}
        >
          <Icon name="close" size={14} />
        </button>
      )}
    </div>
  );
}
