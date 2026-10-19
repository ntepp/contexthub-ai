from src.ingestion.chunking import chunk_pages
from src.ingestion.eu_acts import detect_act
from src.ingestion.loaders import PageText


def test_recognises_regulation_and_nickname():
    act = detect_act("I (Actes législatifs) RÈGLEMENTS RÈGLEMENT (UE) N o 648/2012 DU PARLEMENT EUROPÉEN ET DU CONSEIL")
    assert act is not None
    assert act.official == "Règlement (UE) n° 648/2012"
    assert act.label == "EMIR — Règlement (UE) n° 648/2012"


def test_recognises_directive():
    act = detect_act("DIRECTIVE 2014/65/UE DU PARLEMENT EUROPÉEN ET DU CONSEIL du 15 mai 2014")
    assert act.official == "Directive 2014/65/UE" and act.alias == "MiFID II"


def test_recognises_cross_sector_act():
    act = detect_act("DIRECTIVE (UE) 2022/2555 DU PARLEMENT EUROPÉEN ET DU CONSEIL du 14 décembre 2022")
    assert act.official == "Directive 2022/2555/UE" and act.alias == "NIS 2"


def test_unknown_act_keeps_official_reference():
    act = detect_act("RÈGLEMENT DÉLÉGUÉ (UE) 2017/565 DE LA COMMISSION du 25 avril 2016")
    assert act.official == "Règlement délégué (UE) n° 2017/565" and act.alias is None


def test_internal_document_citing_an_act_is_not_labelled():
    text = "Procédure interne de meilleure exécution. Conformément à la directive 2014/65/UE, la société…"
    assert detect_act(text) is None


def test_doc_label_heads_every_chunk():
    pages = [PageText(1, "Article 2\nDéfinitions\n" + "Aux fins du présent règlement, on entend par contrepartie. " * 5)]
    chunks = chunk_pages(pages, doc_label="EMIR — Règlement (UE) n° 648/2012")
    assert chunks[0].content.startswith("[EMIR — Règlement (UE) n° 648/2012 › Article 2 — Définitions]")
