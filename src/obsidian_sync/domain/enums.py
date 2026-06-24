from enum import StrEnum


class DocumentType(StrEnum):
    REFERENCE = 'reference'
    RULE = 'rule'
    DECISION = 'decision'
    ISSUE_SOLUTION = 'issue-solution'
    STUDY_NOTE = 'study-note'
    PROMPT = 'prompt'
    COMMAND = 'command'
    CHECKLIST = 'checklist'


class DocumentStatus(StrEnum):
    CURRENT = 'current'
    DRAFT = 'draft'
    DEPRECATED = 'deprecated'
    ARCHIVED = 'archived'


class DocumentPriority(StrEnum):
    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'


class DocumentVisibility(StrEnum):
    PERSONAL = 'personal'
    COMPANY = 'company'
    CONFIDENTIAL = 'confidential'
    PUBLIC = 'public'
