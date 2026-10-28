"""民法以外の科目の演習D案（static/gyosei-d/ など）の検査。

画面は民法D案（static/minpo-d/app.js・styles.css）をそのまま使い、科目ごとに持つのは
index.html と chapters.json だけ。民法D案と同じ決まり（各章8問・○4×4・素直／ひっかけの両方に○と×・
全問に本試験の出題箇所）を、科目をまたいで同じように確かめる。
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"

# 科目ごとのページ。章の数は増やしたらここも直す。
# 2026-10-28に利用者が決めた章の数：行政法15・憲法10（民法と同じ作り込み）、商法会社法2・基礎法学1・基礎知識3（最頻出だけ）。
DRILLS = {
    "gyosei-d": {"store": "gyoseiD:v1", "chapters": 15},
    "kenpo-d": {"store": "kenpoD:v1", "chapters": 10},
    "shoho-d": {"store": "shohoD:v1", "chapters": 2},
    "kiso-d": {"store": "kisoD:v1", "chapters": 1},
    "chishiki-d": {"store": "chishikiD:v1", "chapters": 3},
}

REF = re.compile(r"^(平成|令和)(元|[0-9]+)年問([0-9]+)")


def load(name: str) -> dict:
    return json.loads((STATIC / name / "chapters.json").read_text(encoding="utf-8"))


def questions(data: dict) -> dict:
    return {q["id"]: q for c in data["chapters"] for q in c["questions"]}


class DrillDPagesTest(unittest.TestCase):
    def test_pages_share_the_civil_law_engine_with_their_own_store(self) -> None:
        minpo = (STATIC / "minpo-d" / "index.html").read_text(encoding="utf-8")
        version = re.search(r"app\.js\?v=([0-9A-Za-z-]+)", minpo).group(1)
        stores = set()
        for name, conf in DRILLS.items():
            html = (STATIC / name / "index.html").read_text(encoding="utf-8")
            self.assertIn(f'data-store-key="{conf["store"]}"', html)
            self.assertIn("data-drill-name=", html)
            # 民法D案と同じ版のapp.js・styles.cssを読む。版がずれると古い画面が残る。
            self.assertIn(f'src="../minpo-d/app.js?v={version}"', html)
            self.assertIn(f'href="../minpo-d/styles.css?v={version}"', html)
            self.assertIn(conf["store"], html.split("<footer")[1])
            stores.add(conf["store"])
            self.assertFalse((STATIC / name / "app.js").exists(), "画面のコードを複製しない")
        self.assertNotIn("minpoD:v1", stores)
        self.assertEqual(len(stores), len(DRILLS))

    def test_chapters_follow_the_d_rules(self) -> None:
        cards = {
            c["id"]
            for c in json.loads((ROOT / "content" / "explanation_cards.json").read_text(encoding="utf-8"))["items"]
        }
        seen_ids: set[str] = set()
        for name, conf in DRILLS.items():
            data = load(name)
            self.assertEqual("2026-04-01", data["asOf"])
            self.assertEqual(conf["chapters"], len(data["chapters"]), name)
            self.assertEqual(list(range(1, len(data["chapters"]) + 1)), [c["number"] for c in data["chapters"]])
            cast_ids = {c["id"] for c in data["cast"]}
            for chapter in data["chapters"]:
                for field in ("baseScenario", "goal", "remedyGuide", "lead", "title"):
                    self.assertTrue(chapter[field], (chapter["id"], field))
                self.assertTrue(chapter["summaryTable"]["rows"])
                qs = chapter["questions"]
                self.assertEqual(8, len(qs), chapter["id"])
                self.assertEqual(4, sum(q["answer"] for q in qs), chapter["id"])
                for kind in ("straight", "trap"):
                    self.assertEqual(
                        {True, False}, {q["answer"] for q in qs if q["kind"] == kind}, (chapter["id"], kind)
                    )
                self.assertEqual("章の共通事例", qs[0]["delta"]["before"])
                self.assertNotIn("transition", qs[0]["delta"])
                base = {n["id"] for n in chapter["diagram"]["nodes"]}
                for node in chapter["diagram"]["nodes"]:
                    if node.get("castId"):
                        self.assertIn(node["castId"], cast_ids)
                        self.assertIn(node["castId"], chapter["castIds"])
                for q in qs:
                    self.assertNotIn(q["id"], seen_ids)
                    seen_ids.add(q["id"])
                    self.assertIsInstance(q["answer"], bool)
                    for field in (
                        "summary", "fact", "rule", "conclusion", "protectWho",
                        "why", "ifOpposite", "legalSense", "minimum", "next",
                    ):
                        self.assertTrue(q["explanation"].get(field), (q["id"], field))
                    # 答えの記号は解説の先頭と一致させる
                    self.assertTrue(q["explanation"]["summary"].startswith("○" if q["answer"] else "×"), q["id"])
                    hidden = set(q["diagram"]["hiddenNodes"])
                    extra = {n["id"] for n in q["diagram"].get("extraNodes", [])}
                    visible = (base - hidden) | extra
                    self.assertLessEqual(hidden, base)
                    self.assertTrue(q["diagram"]["edges"], q["id"])
                    self.assertTrue(q["answerEdges"], q["id"])
                    for edge in q["diagram"]["edges"] + q["answerEdges"]:
                        self.assertIn(edge["from"], visible, q["id"])
                        self.assertIn(edge["to"], visible, q["id"])
                    for basis in q["legalBasis"]:
                        self.assertTrue(basis["url"].startswith("https://laws.e-gov.go.jp/law/"))
                        self.assertIn("occasion_date=20260401", basis["url"])
                    if q.get("cardId"):
                        self.assertIn(q["cardId"], cards, q["id"])
                    # 過去問で問われた論点だけで作る。全問に年度・問・肢を持ち、問題ページへのリンクを引ける。
                    self.assertTrue(q["examRefs"], q["id"])
                    for ref in q["examRefs"]:
                        era, number, qn = REF.match(ref).groups()
                        year = (1988 if era == "平成" else 2018) + (1 if number == "元" else int(number))
                        self.assertIn(f"{year}-q{qn}", data["examLinks"], (q["id"], ref))
            url = re.compile(r"^https://www\.pro\.goukakudojyo\.com/worksheet2/w_main(nendo|arch)\.php\?queID=[0-9]+$")
            for value in data["examLinks"].values():
                self.assertRegex(value, url)

    def test_cards_and_mock_exam_link_to_every_drill(self) -> None:
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        mock = (STATIC / "mock-exam" / "app.js").read_text(encoding="utf-8")
        for name in ["minpo-d", *DRILLS]:
            self.assertIn(f'dir: "{name}"', app)
            self.assertIn(f"dir: '{name}'", mock)


class GyoseiDLegalBoundaryTest(unittest.TestCase):
    """行政法D案で、取り違えやすい結論を固定する（条文はe-Gov 2026-04-01で確認）。"""

    def test_answers(self) -> None:
        qs = questions(load("gyosei-d"))
        expected = {
            # 申請：審査基準の公表は義務、標準処理期間は努力義務、期間経過でみなし拒否なし、
            # 形式不備は補正か拒否、条例が根拠なら行政手続法は不適用
            "gyosei-d-app-001": True, "gyosei-d-app-002": False, "gyosei-d-app-003": False,
            "gyosei-d-app-004": False, "gyosei-d-app-005": False, "gyosei-d-app-008": True,
            # 不利益処分：処分基準は努力義務、聴聞を求める権利なし、弁明は書面、弁明に閲覧なし、欠席で終結
            "gyosei-d-adv-002": False, "gyosei-d-adv-003": False, "gyosei-d-adv-004": False,
            "gyosei-d-adv-005": True, "gyosei-d-adv-006": False, "gyosei-d-adv-008": True,
            # 行政指導：不利益取扱いの禁止は根拠を問わない、中止の求めは相手方だけ、
            # 地方公共団体の指導は根拠が法律でも不適用、指針は意見公募の対象、届出は到達で済む
            "gyosei-d-gui-002": False, "gyosei-d-gui-004": False, "gyosei-d-gui-005": False,
            "gyosei-d-gui-006": True, "gyosei-d-gui-007": False, "gyosei-d-gui-008": True,
            # 審査請求の入口：条例の処分にも行審法、上級行政庁なしは処分庁へ、初日不算入、
            # 誤った教示は送付、不作為は申請者だけ、再調査でも執行停止
            "gyosei-d-ape-001": True, "gyosei-d-ape-002": False, "gyosei-d-ape-004": False,
            "gyosei-d-ape-006": False, "gyosei-d-ape-007": False, "gyosei-d-ape-008": True,
            # 審理と裁決：審理員は審査庁の職員、口頭意見陳述は義務、参加人も可、本案に理由なしなら停止不要、
            # 上級行政庁は職権で停止できる、諮問は審査庁、不利益変更の禁止、上級行政庁は変更できる
            "gyosei-d-apr-001": False, "gyosei-d-apr-002": False, "gyosei-d-apr-003": True,
            "gyosei-d-apr-004": False, "gyosei-d-apr-005": True, "gyosei-d-apr-006": True,
            "gyosei-d-apr-007": False, "gyosei-d-apr-008": True,
        }
        for qid, answer in expected.items():
            self.assertIs(answer, qs[qid]["answer"], qid)
        self.assertIn("3条3項", qs["gyosei-d-gui-005"]["explanation"]["rule"])
        self.assertIn("翌日", qs["gyosei-d-ape-004"]["explanation"]["summary"])
        self.assertIn("48条", qs["gyosei-d-apr-007"]["explanation"]["rule"])
        later = {
            # 総論：課税処分は明白性なしでも無効になりうる、撤回に別の根拠は不要、委任の範囲外の命令は無効、
            # 裁量基準違反だけでは違法でない、立ち退きは代執行できない、条例の過料は長が科す
            "gyosei-d-ae-002": False, "gyosei-d-ae-003": False, "gyosei-d-ae-005": True,
            "gyosei-d-dl-003": False, "gyosei-d-dl-006": False, "gyosei-d-enf-002": False, "gyosei-d-enf-008": True,
            # 行訴：用途地域×・区画整理○、工事完了で利益消滅、被告は市、審査請求後は裁決から6か月、
            # 職権の執行停止なし、義務付けに出訴期間なし、仮の差止めは申立て
            "gyosei-d-sg-002": False, "gyosei-d-sg-003": False, "gyosei-d-sg-007": False,
            "gyosei-d-sp-001": False, "gyosei-d-sp-004": False, "gyosei-d-sp-005": False,
            "gyosei-d-so-003": False, "gyosei-d-so-006": False,
            # 国賠：公務員個人に請求不可、軽過失は求償不可、取消し不要、2条は無過失、費用負担者も責任
            "gyosei-d-sc1-002": False, "gyosei-d-sc1-003": False, "gyosei-d-sc1-005": False,
            "gyosei-d-sc2-002": False, "gyosei-d-sc2-005": True,
            # 地方自治：手数料は直接請求の対象外、住民監査請求は一人で、住民訴訟は違法だけ、規則は過料だけ、再度の不信任で失職
            "gyosei-d-lr-002": False, "gyosei-d-lr-004": True, "gyosei-d-lr-006": False,
            "gyosei-d-ls-002": False, "gyosei-d-ls-007": False,
            # 2026-10-28の見直しで足した頻出論点：期限は条件でない、公聴会は努力義務、意見ゼロでも結果を公示、
            # 利害関係人にも教示（書面で求めれば書面）、無効確認にも執行停止を準用
            "gyosei-d-ae-009": False, "gyosei-d-app-009": True, "gyosei-d-gui-009": True,
            "gyosei-d-ape-009": True, "gyosei-d-so-009": True,
        }
        for qid, answer in later.items():
            self.assertIs(answer, qs[qid]["answer"], qid)


class OtherDrillLegalBoundaryTest(unittest.TestCase):
    """憲法・商法会社法・基礎法学・基礎知識のD案で、取り違えやすい結論を固定する。"""

    def test_answers(self) -> None:
        expected = {
            "kenpo-d": {
                # 地方選挙権は憲法上の保障なし、私人間は間接適用、尊属殺は区別自体は合憲、夫婦同氏は合憲、
                # 教科書検定は事前抑制でもない、選挙権の制限は広い裁量ではない、予算は再可決不要、
                # 首相は国会議員から、国務大臣は首相が任命、緊急政令は不可、裁判官の懲戒は戒告か過料、決算は検査院が先
                "kenpo-d-hr-002": False, "kenpo-d-hr-006": False, "kenpo-d-eq-001": False, "kenpo-d-eq-006": False,
                "kenpo-d-ex-002": False, "kenpo-d-sp-008": False, "kenpo-d-dt-002": False,
                "kenpo-d-cb-001": False, "kenpo-d-cb-002": False, "kenpo-d-cb-007": False,
                "kenpo-d-jd-004": False, "kenpo-d-fn-003": False,
                "kenpo-d-eq-002": True, "kenpo-d-jd-002": True, "kenpo-d-cb-008": True,
                # 2026-10-28の見直しで足した頻出論点：指紋押なつの自由は外国人にも、容ぼうの撮影は憲法問題になる、
                # 普通教育の教師に完全な教授の自由はない、争議行為の一律禁止は合憲、議院の懲罰に司法審査なし、
                # 単位認定は特段の事情がない限り審査外、予算の増額修正もできる
                "kenpo-d-hr-009": True, "kenpo-d-ex-009": False, "kenpo-d-ae-009": False,
                "kenpo-d-sp-009": False, "kenpo-d-dt-009": True, "kenpo-d-jd-009": False,
                "kenpo-d-fn-009": True,
            },
            "shoho-d": {
                # 引受人は現物出資できない、仮装払込みも総株主の同意で免除できる、創立総会は募集設立だけ、
                # 株主総会では利害関係株主も議決できる、監査役設置会社の議事録閲覧は裁判所の許可、解任は有効
                "shoho-d-in-002": False, "shoho-d-in-004": False, "shoho-d-in-007": False, "shoho-d-in-008": True,
                "shoho-d-og-001": True, "shoho-d-og-002": False, "shoho-d-og-004": False, "shoho-d-og-007": False,
                # 2026-10-28の見直しで足した：発起人は通知の期日まで失権しない、設立時取締役は業務を執行しない、
                # 取締役会の招集通知は1週間前で議題不要
                "shoho-d-in-009": True, "shoho-d-in-010": False, "shoho-d-og-009": True,
            },
            "kiso-d": {
                # 施行期日なしは20日、公布日施行も可、後の一般法は前の特別法を破らない、罰則の経過規定は可
                "kiso-d-law-001": True, "kiso-d-law-002": False, "kiso-d-law-005": False, "kiso-d-law-006": False,
                # 公海上の日本船舶にも及ぶ、推定は覆せるがみなすは覆せない
                "kiso-d-law-009": False, "kiso-d-law-010": True,
            },
            "chishiki-d": {
                # 公務員経験は20年、復権で欠格でなくなる、法人の解散は知事、業務禁止で登録抹消
                "chishiki-d-gs-003": False, "chishiki-d-gs-004": False, "chishiki-d-gs-007": True,
                "chishiki-d-gs-008": False,
                # 事業承継は第三者でない、学術研究機関は一律除外ではない、マイナンバーカードは市町村長
                "chishiki-d-pi-002": False, "chishiki-d-pi-006": False, "chishiki-d-pi-008": False,
                # 外国に住む外国人も開示請求できる、公文書管理法に罰則なし、出生届は14日
                "chishiki-d-rc-002": False, "chishiki-d-rc-006": False, "chishiki-d-rc-008": False,
                # 個人情報は生存する個人（外国人を含む）、外国人住民にも住民票
                "chishiki-d-pi-009": True, "chishiki-d-rc-009": True,
            },
        }
        for name, answers in expected.items():
            qs = questions(load(name))
            for qid, answer in answers.items():
                self.assertIs(answer, qs[qid]["answer"], qid)
        # 行政書士法の令和7年改正（特定行政書士の対象が「行政書士が作成できる書類」に拡大）を反映していること
        gs = questions(load("chishiki-d"))["chishiki-d-gs-002"]
        self.assertIn("令和7年", gs["explanation"]["summary"])
        # 統治の章は配役を使わない（機関の名前を枠にする）
        for chapter in load("kenpo-d")["chapters"]:
            if chapter["id"] in {"diet", "cabinet", "judiciary", "finance-amendment"}:
                self.assertEqual([], chapter["castIds"], chapter["id"])


if __name__ == "__main__":
    unittest.main()
