from plugin.plugins.lifekit._nearby_intent import (
    NearbyIntentRequest,
    NearbyIntentResolver,
    NearbyIntentStatus,
)


def test_broad_nearby_sentence_requests_category_clarification() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request="我附近有啥地方可去吗？",
            proposed_query="我附近有啥地方可去吗？",
        )
    )

    assert result.status is NearbyIntentStatus.NEEDS_CLARIFICATION
    assert result.query == ""
    assert result.choices == ("公园", "景点", "餐厅", "商场")


def test_broad_raw_request_is_not_overridden_by_proposed_category() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request="我附近有啥地方可去吗？",
            proposed_query="公园",
        )
    )

    assert result.status is NearbyIntentStatus.NEEDS_CLARIFICATION
    assert result.query == ""


def test_explicit_category_is_extracted_from_full_sentence() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request="帮我找附近的咖啡店",
            proposed_query="帮我找附近的咖啡店",
        )
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.query == "咖啡店"


def test_multiple_categories_are_not_collapsed_to_one_keyword() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request="附近的咖啡店或者公园",
            proposed_query="咖啡店",
        )
    )

    assert result.status is NearbyIntentStatus.NEEDS_CLARIFICATION
    assert result.query == ""
    assert result.choices == ("咖啡店", "公园")


def test_explicit_admin_location_is_extracted_from_raw_request() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request="吉林市附近的公园",
            proposed_query="吉林市附近的公园",
        )
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.query == "公园"
    assert result.location == "吉林市"


def test_location_extraction_excludes_conversational_prefix() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request="我在北京市朝阳区附近找咖啡店",
            proposed_query="咖啡店",
        )
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.location == "北京市朝阳区"


def test_location_extraction_handles_common_verb_prefixes() -> None:
    cases = {
        "帮我查一下吉林市附近的咖啡店": "吉林市",
        "我想去北京市附近找公园": "北京市",
        "我想去往北京市附近找公园": "北京市",
        "我想到吉林市附近看看": "吉林市",
        "在吉林省吉林市附近找公园": "吉林省吉林市",
    }

    for sentence, expected in cases.items():
        result = NearbyIntentResolver().resolve(
            NearbyIntentRequest(raw_request=sentence, proposed_query=sentence)
        )
        assert result.location == expected


def test_explicit_distance_replaces_default_radius() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request="吉林市附近1公里内的咖啡店",
            proposed_query="咖啡店",
            proposed_radius=3000,
        )
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.radius == 1000


def test_walking_request_caps_search_radius() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request="找走路能到的咖啡店",
            proposed_query="咖啡店",
            proposed_radius=3000,
        )
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.radius == 1500


def test_unrecognised_dialogue_sentence_is_never_used_as_keyword() -> None:
    sentence = "帮我看看附近有没有适合带孩子玩的室内地方"
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(raw_request=sentence, proposed_query=sentence)
    )

    assert result.status is NearbyIntentStatus.NEEDS_CLARIFICATION
    assert result.query == ""


def test_lightly_rewritten_dialogue_sentence_is_not_used_as_keyword() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request="能帮忙搜搜附近适合小朋友去的室内场所吗？",
            proposed_query="能帮忙搜搜附近适合小朋友去的室内场所吗",
        )
    )

    assert result.status is NearbyIntentStatus.NEEDS_CLARIFICATION
    assert result.query == ""


def test_unrecognised_conversational_wording_defaults_to_clarification() -> None:
    sentence = "周围有适合孩子玩的室内场所么"
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request=sentence,
            proposed_query=sentence,
            is_conversational=True,
        )
    )

    assert result.status is NearbyIntentStatus.NEEDS_CLARIFICATION
    assert result.query == ""


def test_broad_request_does_not_execute_selected_substring() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request="周围有适合孩子玩的室内场所么",
            proposed_query="室内",
            is_conversational=True,
        )
    )

    assert result.status is NearbyIntentStatus.NEEDS_CLARIFICATION
    assert result.query == ""


def test_english_broad_request_uses_english_choices() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request="things to do nearby",
            proposed_query="things to do nearby",
            locale="en",
        )
    )

    assert result.status is NearbyIntentStatus.NEEDS_CLARIFICATION
    assert result.choices == ("park", "attraction", "restaurant", "shopping mall")


def test_english_explicit_category_is_extracted() -> None:
    sentence = "Find a coffee shop nearby"
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request=sentence,
            proposed_query=sentence,
            locale="en",
        )
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.query == "coffee shop"


def test_long_but_keyword_shaped_manual_query_remains_executable() -> None:
    query = "children indoor playground"
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request=query,
            proposed_query=query,
            locale="en",
            is_conversational=False,
        )
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.query == query


def test_explicit_manual_values_are_preserved_without_conversation_context() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            proposed_query="亲子乐园",
            proposed_location="吉林市",
            proposed_radius=1200,
        )
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.query == "亲子乐园"
    assert result.location == "吉林市"
    assert result.radius == 1200


def test_manual_radius_is_not_inferred_from_keyword_text() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request="100米生活超市",
            proposed_query="100米生活超市",
            proposed_radius=1200,
            is_conversational=False,
        )
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.radius == 1200


def test_specific_category_is_not_duplicated_by_broader_alias() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request="附近的宠物医院",
            proposed_query="附近的宠物医院",
        )
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.query == "宠物医院"


def test_explicit_location_param_wins_over_raw_text_extraction() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request="吉林市附近的公园",
            proposed_query="公园",
            proposed_location="北京市",
        )
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.location == "北京市"


def test_keyword_tail_is_extracted_without_category_dictionary_entry() -> None:
    sentence = "帮我找附近的亲子乐园"
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(raw_request=sentence, proposed_query=sentence)
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.query == "亲子乐园"


def test_keyword_tail_preserves_category_qualifiers() -> None:
    sentence = "帮我找附近的24小时药店"
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(raw_request=sentence, proposed_query="药店")
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.query == "24小时药店"


def test_keyword_tail_preserves_specific_poi_name() -> None:
    sentence = "帮我找附近的星巴克咖啡"
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(raw_request=sentence, proposed_query="星巴克咖啡")
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.query == "星巴克咖啡"


def test_grounded_specific_poi_query_is_executable() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(raw_request="附近的星巴克", proposed_query="星巴克")
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.query == "星巴克"


def test_verbatim_nearby_noun_phrases_extract_specific_query() -> None:
    cases = {
        "附近的星巴克": "星巴克",
        "吉林市附近的网吧": "网吧",
        "找上海市附近的星巴克": "星巴克",
    }

    for sentence, expected in cases.items():
        result = NearbyIntentResolver().resolve(
            NearbyIntentRequest(
                raw_request=sentence,
                proposed_query=sentence,
                is_conversational=True,
            )
        )
        assert result.status is NearbyIntentStatus.READY
        assert result.query == expected


def test_chinese_nearby_wrappers_are_removed_from_specific_query() -> None:
    cases = {
        "去往北京市附近找公园": "公园",
        "附近有星巴克吗": "星巴克",
        "附近有没有星巴克": "星巴克",
    }

    for sentence, expected in cases.items():
        result = NearbyIntentResolver().resolve(
            NearbyIntentRequest(
                raw_request=sentence,
                proposed_query=sentence,
                is_conversational=True,
            )
        )
        assert result.status is NearbyIntentStatus.READY
        assert result.query == expected


def test_chinese_keyword_beginning_with_you_is_not_corrupted() -> None:
    for sentence in ("附近有机超市", "附近有机超市吗"):
        result = NearbyIntentResolver().resolve(
            NearbyIntentRequest(
                raw_request=sentence,
                proposed_query=sentence,
                is_conversational=True,
            )
        )
        assert result.status is NearbyIntentStatus.READY
        assert result.query == "有机超市"


def test_verbatim_english_nearby_phrase_extracts_specific_query() -> None:
    sentence = "Find Starbucks nearby"
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request=sentence,
            proposed_query=sentence,
            locale="en",
            is_conversational=True,
        )
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.query == "Starbucks"


def test_english_bare_and_existential_nearby_phrases_extract_query() -> None:
    cases = {
        "Starbucks nearby": "Starbucks",
        "Are there Starbucks nearby?": "Starbucks",
        "Is there a Starbucks nearby?": "Starbucks",
    }

    for sentence, expected in cases.items():
        result = NearbyIntentResolver().resolve(
            NearbyIntentRequest(
                raw_request=sentence,
                proposed_query=sentence,
                locale="en",
                is_conversational=True,
            )
        )
        assert result.status is NearbyIntentStatus.READY
        assert result.query == expected


def test_english_open_questions_do_not_become_poi_keywords() -> None:
    for sentence in (
        "What is nearby?",
        "Where can I go nearby?",
        "What restaurants are nearby?",
    ):
        result = NearbyIntentResolver().resolve(
            NearbyIntentRequest(
                raw_request=sentence,
                proposed_query=sentence,
                locale="en",
                is_conversational=True,
            )
        )
        assert result.status is NearbyIntentStatus.NEEDS_CLARIFICATION
        assert result.query == ""


def test_poi_name_starting_with_conjunction_character_is_not_split() -> None:
    sentence = "帮我找附近的和睦家"
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(raw_request=sentence, proposed_query="和睦家")
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.query == "和睦家"


def test_terse_poi_name_containing_category_alias_is_not_broadened() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(raw_request="和平饭店", proposed_query="和平饭店")
    )

    assert result.status is NearbyIntentStatus.READY
    assert result.query == "和平饭店"


def test_unknown_parallel_keyword_tails_request_clarification() -> None:
    sentence = "帮我找附近的花店和洗衣店"
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(raw_request=sentence, proposed_query=sentence)
    )

    assert result.status is NearbyIntentStatus.NEEDS_CLARIFICATION
    assert result.choices == ("花店", "洗衣店")


def test_mixed_known_and_unknown_keyword_tails_request_clarification() -> None:
    sentence = "帮我找附近的咖啡店和洗衣店"
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(raw_request=sentence, proposed_query=sentence)
    )

    assert result.status is NearbyIntentStatus.NEEDS_CLARIFICATION
    assert result.choices == ("咖啡店", "洗衣店")


def test_common_single_character_conjunctions_request_clarification() -> None:
    for separator in ("或", "跟", "及"):
        sentence = f"帮我找附近的花店{separator}洗衣店"
        result = NearbyIntentResolver().resolve(
            NearbyIntentRequest(raw_request=sentence, proposed_query=sentence)
        )
        assert result.status is NearbyIntentStatus.NEEDS_CLARIFICATION
        assert result.choices == ("花店", "洗衣店")


def test_traditional_chinese_category_choices_are_localized() -> None:
    result = NearbyIntentResolver().resolve(
        NearbyIntentRequest(
            raw_request="附近的咖啡廳或公園",
            proposed_query="咖啡廳",
            locale="zh-TW",
        )
    )

    assert result.status is NearbyIntentStatus.NEEDS_CLARIFICATION
    assert result.choices == ("咖啡廳", "公園")
