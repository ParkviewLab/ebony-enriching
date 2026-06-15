# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""ebony-enriching — MCP server: the lab notebook substrate.

Records proposals (hypotheses), experiments (test runs), and gap signals.
Reads/writes only — does not enforce policy, does not run tests. The
agents using this server own any cross-system orchestration.
"""
