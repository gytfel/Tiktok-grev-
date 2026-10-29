import { useRef, useState } from "react";
import type { DragEvent } from "react";
import { ApiError, uploadWithProgress } from "../api";
import type { Media } from "../types";
import { useToast } from "./ui";

export function VideoUploader({ onUploaded }: { onUploaded: (media: Media) => void }) {
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);

  async function upload(file: File) {
    const form = new FormData();
    form.append("file", file);
    setProgress(0);
    try {
      const media = await uploadWithProgress<Media>("/api/media", form, setProgress);
      if (media.validation_status === "valid") toast(`«${media.original_filename}» загружено`, "success");
      else toast(`«${media.original_filename}» не подходит для TikTok: ${media.validation_issues[0]}`, "danger");
      onUploaded(media);
    } catch (e) {
      toast((e as ApiError).message, "danger");
    } finally {
      setProgress(null);
      if (input.current) input.current.value = "";
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) upload(file);
  }

  return (
    <div
      className={`dropzone ${dragging ? "dragging" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      {progress === null ? (
        <>
          <p>
            Перетащите видео сюда или{" "}
            <button className="link-btn strong" onClick={() => input.current?.click()}>
              выберите файл
            </button>
          </p>
          <p className="muted small">MP4, MOV или WebM · H.264/H.265/VP8/VP9 · 23–60 кадр/с · сторона от 360 до 4096 px · до 4 ГБ</p>
        </>
      ) : (
        <div className="upload-progress">
          <span>{progress < 1 ? `Загрузка… ${Math.round(progress * 100)}%` : "Проверка файла…"}</span>
          <div className="progress">
            <div style={{ width: `${Math.round(progress * 100)}%` }} />
          </div>
        </div>
      )}
      <input
        ref={input}
        type="file"
        accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm"
        hidden
        onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
      />
    </div>
  );
}
