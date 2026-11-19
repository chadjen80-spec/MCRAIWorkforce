"use client";

import { useState } from "react";
import { ArtifactGrid } from "@/components/artifacts";
import { Button, EmptyState, ErrorState, LoadingBlock, PageHeader, Pagination, RefreshError } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { api, stabilizeArtifacts, toApiError } from "@/lib/api";
import { ARTIFACT_KINDS, type Artifact, type ArtifactKind, type Paginated } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const LIMIT = 18;
// Signed download URLs expire (15 min by default); refresh the list well inside that window.
const REFRESH_MS = 4 * 60 * 1000;

export default function DeliverablesPage() {
  const { t } = useI18n();
  const [kind, setKind] = useState<ArtifactKind | "">("");
  const [offset, setOffset] = useState(0);

  const artifacts = useApi<Paginated<Artifact>>(
    `artifacts:${kind}:${offset}`,
    async (signal) => {
      const page = await api<Paginated<Artifact>>("/v1/artifacts", { query: { kind, limit: LIMIT, offset }, signal });
      return { ...page, items: stabilizeArtifacts(page.items) };
    },
    { interval: REFRESH_MS },
  );

  // Delete every stored file in the current list (respects the type filter). The jobs
  // themselves stay; files of jobs still in progress are skipped by the server.
  const [clearing, setClearing] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const total = artifacts.data?.total ?? 0;
  const deleteAll = async () => {
    if (!window.confirm(t("deliverables.deleteAllConfirm", { n: total }))) return;
    setClearing(true);
    setNotice(null);
    try {
      const result = await api<{ deleted: number; skipped_active: number }>("/v1/artifacts", { method: "DELETE", query: { kind } });
      setNotice({
        tone: "ok",
        text:
          t("deliverables.deleteAllDone", { n: result.deleted }) +
          (result.skipped_active ? t("deliverables.deleteAllSkipped", { n: result.skipped_active }) : ""),
      });
      setOffset(0);
      artifacts.reload();
    } catch (e) {
      setNotice({ tone: "bad", text: t("deliverables.deleteFailed", { message: toApiError(e).message }) });
    }
    setClearing(false);
  };

  return (
    <>
      <PageHeader title={t("deliverables.title")} subtitle={t("deliverables.subtitle")} />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <label htmlFor="kind-filter" className="text-sm text-dim">
          {t("deliverables.kindFilter")}
        </label>
        <select
          id="kind-filter"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value as ArtifactKind | "");
            setOffset(0);
          }}
          className="input w-auto min-w-44"
        >
          <option value="">{t("common.all")}</option>
          {ARTIFACT_KINDS.map((k) => (
            <option key={k} value={k}>
              {t(`kind.${k}`)}
            </option>
          ))}
        </select>
        {total > 0 && (
          <Button variant="danger" loading={clearing} onClick={deleteAll} className="ml-auto">
            {kind ? t("deliverables.deleteAllFiltered", { kind: t(`kind.${kind}`) }) : t("deliverables.deleteAll")}
          </Button>
        )}
      </div>
      {notice && (
        <p role="status" className={notice.tone === "ok" ? "mb-4 text-sm text-ok" : "mb-4 text-sm text-bad"}>
          {notice.text}
        </p>
      )}

      {artifacts.loading && <LoadingBlock />}
      {artifacts.error && !artifacts.data && <ErrorState error={artifacts.error} onRetry={artifacts.reload} />}
      {artifacts.error && artifacts.data && <RefreshError error={artifacts.error} onRetry={artifacts.reload} />}

      {artifacts.data &&
        (artifacts.data.items.length === 0 ? (
          <EmptyState
            title={kind ? t("deliverables.emptyFiltered") : t("deliverables.empty")}
            hint={kind ? undefined : t("deliverables.emptyHint")}
          />
        ) : (
          <>
            <ArtifactGrid artifacts={artifacts.data.items} showJobLink onChanged={artifacts.reload} />
            <Pagination total={artifacts.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        ))}
    </>
  );
}
