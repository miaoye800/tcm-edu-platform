from app.profile.modes import _identity_cards, _quiz_cards, _resource_card, load_diagnostic_questions


def test_diagnostic_questions_carry_random_herb_identity_metadata() -> None:
    questions = load_diagnostic_questions(limit=5, photo_ready_only=True)

    assert questions
    assert all(question.get("herb_name") for question in questions)
    assert all(question.get("herb_id") for question in questions)
    assert all(question["herb_id"] in {"herb_taizishen", "herb_dangshen", "herb_huangqi", "herb_chenpi", "herb_jinyinhua"} for question in questions)
    assert all(question["skill"] == "功效识记" for question in questions)
    assert len({question["herb_name"] for question in questions}) >= 2


def test_photo_quiz_has_illustrated_fallback_and_general_pool_stays_broad(tmp_path) -> None:
    fallback = load_diagnostic_questions(limit=5, data_dir=tmp_path, photo_ready_only=True)
    general_pool = load_diagnostic_questions(limit=500)

    assert len(fallback) == 5
    assert all(question.get("herb_id") and question.get("herb_name") for question in fallback)
    assert any(question["herb_id"] not in {"herb_taizishen", "herb_dangshen", "herb_huangqi", "herb_chenpi", "herb_jinyinhua"} for question in general_pool)


def test_free_text_answer_becomes_component_evidence_and_improves_detail_signal() -> None:
    question = {
        "id": "q-jinyinhua",
        "answer": "清热解毒",
        "topic": "清热药·金银花功效",
        "skill": "功效识记",
    }
    cards = _quiz_cards(
        {
            "answers": [
                {
                    "question_id": "q-jinyinhua",
                    "selected": "",
                    "free_text": "甘寒，清热解毒，疏散风热；咽喉肿痛时可与连翘相须。",
                }
            ]
        },
        [question],
        source="一幕·药圃初认",
        response_time_ms=1000,
    )

    measured = next(card for card in cards if card.component == "measured_performance")
    concept = next(card for card in cards if card.component == "concept_foundation")
    assert measured.collection_status == "scorable"
    assert measured.objective_signal["correct"] is True
    assert measured.objective_signal["answer_detail_length"] > 20
    assert concept.quote and "清热解毒" in concept.quote
    assert concept.objective_signal["answer_quality"] > 0.8


def test_detailed_free_text_accepts_another_verified_effect() -> None:
    question = {
        "id": "q-taizishen",
        "answer": "益气健脾",
        "accepted_answers": ["益气健脾", "生津润肺"],
        "topic": "补虚药·太子参功效",
        "skill": "功效识记",
    }
    cards = _quiz_cards(
        {"answers": [{"question_id": "q-taizishen", "free_text": "太子参味甘微苦、性平，能生津润肺。"}]},
        [question],
        source="一幕·药圃初认",
        response_time_ms=1000,
    )

    measured = next(card for card in cards if card.component == "measured_performance")
    assert measured.objective_signal["correct"] is True
    assert measured.objective_signal["answer_quality"] > 0.72


def test_custom_identity_and_resource_are_preserved_as_traceable_evidence() -> None:
    identity = _identity_cards(
        {
            "identity_card": "临床学徒",
            "identity_custom": "我是护理专业学生",
            "utterance": "想先把常用药性学扎实",
        },
        source="序幕·叩问初心",
        response_time_ms=900,
        prior_weight=0.3,
    )
    resource = _resource_card(
        {"resource_type": "", "resource_custom": "带图片的病例短剧", "response_time_ms": 700},
        source="三幕·师父药宝",
        response_time_ms=700,
    )

    assert "护理专业学生" in identity[0].fact
    assert "常用药性" in identity[0].fact
    assert resource.component == "preference_specificity"
    assert resource.objective_signal["custom_text"] == "带图片的病例短剧"
    assert resource.quote == "带图片的病例短剧"
