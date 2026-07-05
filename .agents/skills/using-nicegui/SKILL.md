---
name: using-nicegui
description: When you're writing web interfaces with NiceGUI, you should use this skill.
---

<!-- This skill was last updated on 2026-03-26 -->

# Using NiceGUI

## NiceGUI Documentation Guideline

If you want to learn how to use a specific component or feature of it, you can use *curl* or *webpage fetching tool* to access https://github.com/zauberzeug/nicegui/raw/main/website/documentation/content/{filename}_documentation.py to get detailed documentation, where `{filename}` can be:

add_static_files, add_style, aggrid, altair, anywidget, audio, avatar, badge, button, button_dropdown, button_group, card, carousel, chat_message, checkbox, chip, circular_progress, clipboard, code, codemirror, color_input, color_picker, colors, column, context_menu, dark_mode, date, date_input, dialog, download, echart, editor, element, element_filter, event, expansion, fab, fullscreen, generic_events, grid, highchart, html, icon, image, input_chips, input, interactive_image, joystick, json_editor, keyboard, knob, label, leaflet, line_plot, linear_progress, link, list, log, markdown, matplotlib, menu, mermaid, navigate, notification, notify, number, page, page_layout, page_title, pagination, plotly, pyplot, query, radio, range, rating, refreshable, restructured_text, row, run, run_javascript, scene, screen, scroll_area, select, separator, skeleton, slide_item, slider, space, spinner, splitter, stepper, storage, sub_pages, switch, table, tabs, teleport, textarea, time, time_input, timeline, timer, toggle, tooltip, tree, upload, user, video, xterm.

Since NiceGUI is a rapidly evolving project, the documentation may be updated frequently. Therefore, it's *strongly recommended* to check the official NiceGUI documentation for up-to-date information and examples. Never write code based on outdated knowledge.

## NiceGUI Useful Tips

NiceGUI uses *Quasar* framework for its frontend components, so you can migrate Quasar features to NiceGUI if you find something you need in Quasar but not in NiceGUI. For example, you can use `.props()` method to set some *properties* that are not directly supported by NiceGUI, or use *slot* technique to achieve some complex layout.
