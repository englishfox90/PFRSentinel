"""Find or open the dev builds Discussions thread for the current release cycle.

One thread per release line ("Dev builds: 3.7.7") collects every dev build
leading up to that release, one comment per build. ``prepare`` runs before the
release is replaced, so a missing category fails while the previous build is
still downloadable:

- finds this cycle's thread, or opens it; reopens it if it was closed (a
  dispatch from a branch on another release line closes it, see below)
- never edits a post: GITHUB_TOKEN can create, comment on, close and reopen a
  discussion, but updateDiscussion is refused for it (checked 2026-09-17: the
  same mutation succeeds with a user token). So the opening post is fixed at
  creation and each build comment carries the change lists
- closes every other open dev builds thread the workflow opened, with a
  comment pointing at the current one. That is how the previous cycle's thread
  retires once its release ships, and how the original single thread retired.

Only threads authored by the workflow are ever commented on or closed. A person can
post in the category, and a title starting "Dev builds:" does not make their
thread the workflow's to close.

Neither creating the category nor pinning a thread is possible through the
API; both stay manual.

Stdlib and the ``gh`` CLI only: it runs on the publish job's system Python.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dev_build_notes as notes  # noqa: E402

BOT_LOGIN = "github-actions"

GraphQL = Callable[[str, dict[str, str]], dict]

_REPOSITORY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    id
    discussionCategories(first: 50) { nodes { id name } }
  }
}"""

_DISCUSSIONS = """
query($owner: String!, $name: String!, $category: ID!) {
  repository(owner: $owner, name: $name) {
    discussions(first: 100, categoryId: $category,
                orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes { id number url title closed author { login } }
    }
  }
}"""

_CREATE = """
mutation($repo: ID!, $category: ID!, $title: String!, $body: String!) {
  createDiscussion(input: {repositoryId: $repo, categoryId: $category,
                           title: $title, body: $body}) {
    discussion { id number url title closed author { login } }
  }
}"""

_REOPEN = """
mutation($id: ID!) {
  reopenDiscussion(input: {discussionId: $id}) { discussion { id } }
}"""

_CLOSE = """
mutation($id: ID!) {
  closeDiscussion(input: {discussionId: $id, reason: OUTDATED}) { discussion { id } }
}"""

_COMMENT = """
mutation($id: ID!, $body: String!) {
  addDiscussionComment(input: {discussionId: $id, body: $body}) { comment { url } }
}"""


class CategoryMissing(RuntimeError):
    pass


class GraphQLFailed(RuntimeError):
    pass


def gh_graphql(query: str, variables: dict[str, str]) -> dict:
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        # -f, not -F: -F would turn a body of "true" or "42" into a non-string.
        cmd += ["-f", f"{key}={value}"]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        # The failure reason is only in gh's output; CalledProcessError would
        # print the whole command (every post body) and none of the reason.
        operation = query.strip().splitlines()[0]
        raise GraphQLFailed(f"{operation} -> {(done.stderr or done.stdout).strip()}")
    return json.loads(done.stdout)["data"]


def _is_ours(discussion: dict) -> bool:
    return (discussion.get("author") or {}).get("login") == BOT_LOGIN


def prepare(version: str, gql: GraphQL,
            repository: str = notes.REPO) -> dict:
    owner, name = repository.split("/", 1)
    repo = gql(_REPOSITORY, {"owner": owner, "name": name})["repository"]
    category = next((c["id"] for c in repo["discussionCategories"]["nodes"]
                     if c["name"] == notes.DISCUSSION_CATEGORY), None)
    if category is None:
        raise CategoryMissing(
            f"No '{notes.DISCUSSION_CATEGORY}' Discussions category. Create it under "
            "Settings > General > Discussions, then re-run.")

    existing = gql(_DISCUSSIONS, {"owner": owner, "name": name, "category": category})
    threads = [d for d in existing["repository"]["discussions"]["nodes"] if _is_ours(d)]

    title = notes.discussion_title(version)
    body = notes.discussion_intro(version)
    current = next((d for d in threads if d["title"] == title), None)
    created = current is None

    if created:
        current = gql(_CREATE, {"repo": repo["id"], "category": category,
                                "title": title, "body": body})["createDiscussion"]["discussion"]
    elif current["closed"]:
        gql(_REOPEN, {"id": current["id"]})

    retired = []
    for thread in threads:
        if (thread["id"] == current["id"] or thread["closed"]
                or not thread["title"].startswith(notes.DISCUSSION_TITLE_PREFIX)):
            continue
        gql(_COMMENT, {"id": thread["id"],
                       "body": notes.superseded_comment(title, current["url"])})
        gql(_CLOSE, {"id": thread["id"]})
        retired.append(thread["url"])

    return {"id": current["id"], "url": current["url"], "title": title,
            "created": created, "retired": retired}


def add_comment(discussion_id: str, body: str, gql: GraphQL) -> str:
    return gql(_COMMENT, {"id": discussion_id, "body": body})["addDiscussionComment"]["comment"]["url"]


def _write_outputs(values: dict[str, str]) -> None:
    lines = "".join(f"{key}={value}\n" for key, value in values.items())
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(lines)
    else:
        sys.stdout.write(lines)


def main(argv: list[str] | None = None, gql: GraphQL = gh_graphql) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prepare", help="Find or open this cycle's thread")
    prep.add_argument("--version", required=True)

    comment = sub.add_parser("comment", help="Post a build comment to a thread")
    comment.add_argument("--id", required=True)
    comment.add_argument("--body-file", required=True, type=Path)

    args = parser.parse_args(argv)
    repository = os.environ.get("GITHUB_REPOSITORY", notes.REPO)

    if args.command == "prepare":
        try:
            result = prepare(args.version, gql, repository)
        except CategoryMissing as exc:
            print(f"::error::{exc}")
            return 1
        if result["created"]:
            print(f"::notice::Opened {result['title']} at {result['url']} - pin it by hand.")
        for url in result["retired"]:
            print(f"Closed superseded thread {url}")
        _write_outputs({"id": result["id"], "url": result["url"]})
    else:
        print(add_comment(args.id, args.body_file.read_text(encoding="utf-8"), gql))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
