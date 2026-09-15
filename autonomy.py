"""Bounded autonomous development: one project, one transaction, verified completion."""
from __future__ import annotations
import copy
import json
import os
import pathlib
import re
import threading
import time
import uuid
from urllib.parse import urlsplit
from core import CodeStudioCore, truncate, SCHEMA, ModelOutputError
from workflow import normalize_job, job_result
from runmemory import save_candidate, load_candidate, candidate_signature, record_success, run_id
from moduleflow import normalize_workflow, MAX_REFERENCES
from failureanalysis import analyze_failure, blocker_report
from plan_gate import acceptance_for_prompt, ensure_checks, lint_plan_acceptance, normalize_acceptance
from processrunner import run_command, ProcessTreeUncertain
from safety import WorkspaceTransaction, atomic_bytes, canonical, digest, identity, relative, redact, ensure_source_text, safe_path

# NOTE: truncated mid-write by executor — DO NOT COMMIT THIS; see FOLLOWUP
