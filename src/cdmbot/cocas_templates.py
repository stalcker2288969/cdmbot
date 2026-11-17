"""Resolve template constants before CoCAS 0.2.3 encodes instructions and storage."""
from dataclasses import fields, is_dataclass

from cocas.assembler import assembler, object_generator
from cocas.assembler.ast_nodes import RelocatableExpressionNode, TemplateFieldNode


def install():
    original = object_generator.generate_object_module
    if getattr(original, '_template_subtraction_fixed', False):
        return

    def generate(program, target):
        templates = {section.name: object_generator.Template(section, target).labels for section in program.template_sections}

        def visit(node):
            if isinstance(node, RelocatableExpressionNode):
                for attribute, sign in [('add_terms', 1), ('sub_terms', -1)]:
                    remaining = []
                    for term in getattr(node, attribute):
                        resolved = object_generator.label_or_template(term, templates)
                        if isinstance(resolved, TemplateFieldNode) and resolved.field_name in templates[resolved.template_name]:
                            node.const_term += sign * templates[resolved.template_name][resolved.field_name]
                        else:
                            remaining.append(term)
                    setattr(node, attribute, remaining)
            elif isinstance(node, list):
                for child in node:
                    visit(child)
            elif is_dataclass(node):
                for field in fields(node):
                    visit(getattr(node, field.name))

        visit(program.absolute_sections + program.relocatable_sections)
        return original(program, target)

    generate._template_subtraction_fixed = True
    object_generator.generate_object_module = generate
    assembler.generate_object_module = generate
