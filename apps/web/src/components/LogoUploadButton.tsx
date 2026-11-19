"use client";

import { useRef, useState } from "react";
import { Button, ErrorState } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { ApiError, toApiError } from "@/lib/api";
import { uploadLogos } from "@/lib/poster";
import type { BrandAsset } from "@/lib/types";

/** "Add sponsor / presenter logos": pick image files, upload them to the logo library, hand them back. */
export function LogoUploadButton({
  label,
  onUploaded,
  disabled,
  multiple = true,
  kind = "logo",
}: {
  label: string;
  /** "artwork": a photo for the artwork library (e.g. the venue photo) instead of a logo. */
  kind?: BrandAsset["kind"];
  /** false: pick exactly one file (the event logo). */
  multiple?: boolean;
  onUploaded: (logos: BrandAsset[]) => void;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const input = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const onFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setError(null);
    setUploading(true);
    try {
      onUploaded(
        await uploadLogos(
          files,
          kind === "artwork" ? t("poster.artTooLarge") : t("poster.tooLarge"),
          kind,
        ),
      );
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="space-y-2">
      <input
        ref={input}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        multiple={multiple}
        hidden
        aria-label={label}
        onChange={(e) => {
          void onFiles(e.target.files);
          e.target.value = "";
        }}
      />
      <Button
        loading={uploading}
        disabled={disabled}
        onClick={() => input.current?.click()}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className="size-4"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
        </svg>
        {uploading ? t("poster.uploading") : label}
      </Button>
      {error && <ErrorState error={error} compact />}
    </div>
  );
}
