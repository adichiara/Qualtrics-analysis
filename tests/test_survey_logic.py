from qualtrics_pipeline.survey_logic import (
    build_display_logic_map,
    evaluate,
    parse_display_logic,
)


# A Qualtrics-shaped DisplayLogic: show if QID2 choice 1 Is Selected.
_SINGLE = {
    "0": {
        "0": {
            "LogicType": "Question",
            "Operator": "Selected",
            "ChoiceLocator": "q://QID2/SelectableChoice/1",
            "QuestionID": "QID2",
            "Type": "Expression",
        },
        "Type": "If",
    },
    "Type": "BooleanExpression",
}

# Two conditions joined by Or (note Qualtrics' "Conjuction" misspelling).
_OR = {
    "0": {
        "0": {
            "LogicType": "Question",
            "Operator": "Selected",
            "ChoiceLocator": "q://QID3/SelectableChoice/1",
            "Type": "Expression",
        },
        "1": {
            "LogicType": "Question",
            "Operator": "Selected",
            "ChoiceLocator": "q://QID3/SelectableChoice/4",
            "Conjuction": "Or",
            "Type": "Expression",
        },
        "Type": "If",
    },
    "Type": "BooleanExpression",
}

_META = {
    "QID2": {"DataExportTag": "Q1.5", "QuestionType": "MC", "Selector": "SAVR",
             "RecodeValues": {"1": "1", "2": "2", "3": "3"}, "Choices": {"1": {}, "2": {}, "3": {}}},
    # QID3 choice "4" recodes to "5" — evaluator must use the recode value.
    "QID3": {"DataExportTag": "Q1.6", "QuestionType": "MC", "Selector": "SAVR",
             "RecodeValues": {"1": "1", "4": "5"}, "Choices": {"1": {}, "4": {}}},
    "QID_MULTI": {"DataExportTag": "Q9", "QuestionType": "MC", "Selector": "MAVR",
                  "RecodeValues": {"3": "3"}, "Choices": {"3": {}}},
}


def test_parse_single_selected_uses_recode_column() -> None:
    tree = parse_display_logic(_SINGLE, _META)
    assert tree == {"type": "pred", "column": "Q1.5", "op": "equals", "value": "1"}


def test_parse_or_uses_recode_values() -> None:
    tree = parse_display_logic(_OR, _META)
    assert tree["type"] == "or"
    assert {"type": "pred", "column": "Q1.6", "op": "equals", "value": "1"} in tree["operands"]
    # choice 4 -> recode "5"
    assert {"type": "pred", "column": "Q1.6", "op": "equals", "value": "5"} in tree["operands"]


def test_parse_multi_answer_targets_binary_column() -> None:
    dl = {
        "0": {"0": {"LogicType": "Question", "Operator": "Selected",
                    "ChoiceLocator": "q://QID_MULTI/SelectableChoice/3", "Type": "Expression"},
              "Type": "If"},
    }
    tree = parse_display_logic(dl, _META)
    assert tree == {"type": "pred", "column": "Q9_3", "op": "equals", "value": "1"}


def test_parse_boolean_value_constant() -> None:
    dl = {"0": {"0": {"LogicType": "BooleanValue", "Value": "False", "Type": "Expression"}, "Type": "If"}}
    assert parse_display_logic(dl, _META) == {"type": "const", "value": False}


def test_unsupported_operator_flags_not_evaluable() -> None:
    dl = {
        "0": {"0": {"LogicType": "EmbeddedField", "Operator": "EqualTo",
                    "LeftOperand": "foo", "Type": "Expression"}, "Type": "If"},
    }
    meta = {"Q": {"DisplayLogic": dl}}
    result = build_display_logic_map(meta)
    assert result["Q"]["fully_evaluable"] is False


def test_evaluate_predicate_and_blanks() -> None:
    tree = {"type": "pred", "column": "Q1.5", "op": "equals", "value": "1"}
    assert evaluate(tree, {"Q1.5": "1"}) is True
    assert evaluate(tree, {"Q1.5": "2"}) is False
    assert evaluate(tree, {"Q1.5": ""}) is False
    assert evaluate(tree, {}) is False


def test_evaluate_and_or_nodes() -> None:
    and_tree = {"type": "and", "operands": [
        {"type": "pred", "column": "A", "op": "equals", "value": "1"},
        {"type": "pred", "column": "B", "op": "equals", "value": "1"},
    ]}
    assert evaluate(and_tree, {"A": "1", "B": "1"}) is True
    assert evaluate(and_tree, {"A": "1", "B": "0"}) is False

    or_tree = {"type": "or", "operands": and_tree["operands"]}
    assert evaluate(or_tree, {"A": "1", "B": "0"}) is True
    assert evaluate(or_tree, {"A": "0", "B": "0"}) is False


def test_not_selected_uses_not_equals() -> None:
    dl = {
        "0": {"0": {"LogicType": "Question", "Operator": "NotSelected",
                    "ChoiceLocator": "q://QID2/SelectableChoice/1", "Type": "Expression"},
              "Type": "If"},
    }
    tree = parse_display_logic(dl, _META)
    assert tree == {"type": "pred", "column": "Q1.5", "op": "not_equals", "value": "1"}
    assert evaluate(tree, {"Q1.5": "2"}) is True
    assert evaluate(tree, {"Q1.5": "1"}) is False
