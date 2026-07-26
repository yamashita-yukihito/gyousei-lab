(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", loadInventory);

  async function loadInventory() {
    try {
      const response = await fetch("api/data-inventory", { cache: "no-store" });
      const inventory = await response.json();
      if (!response.ok || !inventory.available) {
        throw new Error(inventory.message || "集計を利用できません");
      }
      renderInventory(inventory);
      document.getElementById("inventory-loading").hidden = true;
      document.getElementById("inventory-view").hidden = false;
    } catch (error) {
      document.getElementById("inventory-loading").hidden = true;
      const message = document.getElementById("inventory-error");
      message.hidden = false;
      message.textContent = "データ件数を読み込めませんでした。過去問ラボの「データ状態」でも確認できます。";
      console.warn(error);
    }
  }

  function renderInventory(inventory) {
    const coverage = inventory.coverage;
    const allScope = inventory.scopes.find((scope) => scope.id === "all");
    const totals = allScope.totals;
    setText("coverage-years", coverage.firstExamYear + "〜" + coverage.lastExamYear + "年");
    setText("coverage-stored", count(totals.questionUnits) + "問");
    setText("coverage-choices", count(totals.regularChoiceCount) + "肢");
    setText("coverage-safe", count(totals.safeOxChoiceCount) + "肢");

    const body = document.getElementById("inventory-body");
    body.replaceChildren(...allScope.subjects.map(subjectRow));
    const total = document.createElement("tr");
    appendCells(total, [
      "全分野",
      count(totals.questionUnits),
      count(totals.regularChoiceCount),
      count(totals.safeOxChoiceCount),
      count(totals.multipleBlankQuestions),
      count(totals.wordBankEntryCount),
      count(totals.blankSlotCount),
      count(totals.writtenQuestions)
    ]);
    document.getElementById("inventory-total").replaceChildren(total);

    const publicMissing = coverage.omissions.find((item) => item.kind === "publicTextUnavailable");
    const indexMissing = coverage.omissions.find((item) => item.kind === "providerIndexAbsent");
    const parts = [
      "理論上は60問×" + coverage.yearCount + "年＝" + count(coverage.expectedQuestionUnits) + "問です。"
    ];
    if (publicMissing) {
      parts.push("問58〜60の本文" + count(publicMissing.questionUnits) + "問分は、著作権上の理由で公開過去問に含まれません。");
    }
    if (indexMissing) {
      parts.push(indexMissing.examYear + "年問" + indexMissing.questionNumber + "が取得元一覧にないため、保存数は" + count(coverage.storedQuestionUnits) + "問です。");
    }
    setText("coverage-gap", parts.join(" "));
  }

  function subjectRow(subject) {
    const row = document.createElement("tr");
    appendCells(row, [
      subject.subjectLabel,
      count(subject.questionUnits),
      count(subject.regularChoiceCount),
      count(subject.safeOxChoiceCount),
      count(subject.multipleBlankQuestions),
      count(subject.wordBankEntryCount),
      count(subject.blankSlotCount),
      count(subject.writtenQuestions)
    ]);
    return row;
  }

  function appendCells(row, values) {
    values.forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    });
  }

  function setText(id, value) {
    document.getElementById(id).textContent = value;
  }

  function count(value) {
    return Number.isFinite(Number(value)) ? Number(value).toLocaleString("ja-JP") : "—";
  }
}());
