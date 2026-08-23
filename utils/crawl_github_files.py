import requests
import base64
import os
import tempfile
import git
import time
import fnmatch
from concurrent.futures import ThreadPoolExecutor
from typing import Union, Set, List, Dict, Tuple, Any, Callable, Optional
from urllib.parse import quote, urlparse

# Downloads are independent, and a serial crawl spends ~1.6s of round trip per file.
GITHUB_MAX_CONCURRENCY = int(os.getenv("GITHUB_MAX_CONCURRENCY", "16"))

TRANSIENT_HTTP_ERRORS = (
    requests.exceptions.SSLError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


class DownloadIncompleteError(Exception):
    """Raised when wanted files could not be downloaded after retries."""


def path_under_base(item_path: str, base_prefix: str) -> bool:
    # Compare on a path boundary: a bare startswith also matches sibling directories
    # such as "cookbook/agent-skills" when the base is "cookbook/agent".
    prefix = (base_prefix or "").rstrip("/")
    return not prefix or item_path.startswith(prefix + "/") or item_path == prefix


def relative_to_base_path(item_path: str, base_prefix: str, use_relative_paths: bool) -> str:
    prefix = (base_prefix or "").rstrip("/")
    if use_relative_paths and prefix and path_under_base(item_path, prefix):
        return item_path[len(prefix):].lstrip("/")
    return item_path


def sort_files(files: dict) -> list:
    return sorted(files.items(), key=lambda item: item[0])


def raise_if_downloads_incomplete(failed: list, wanted: int) -> None:
    if failed:
        preview = ", ".join(failed[:5])
        extra = "" if len(failed) <= 5 else f" (+{len(failed) - 5} more)"
        raise DownloadIncompleteError(
            f"Failed to download {len(failed)}/{wanted} files: {preview}{extra}"
        )


def is_rate_limited(response) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code == 403 and "rate limit" in (response.text or "").lower():
        return True
    return False


def rate_limit_wait(response) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(60.0, max(0.5, float(retry_after)))
        except ValueError:
            pass
    reset = response.headers.get("X-RateLimit-Reset")
    if reset:
        try:
            return min(60.0, max(0.5, float(reset) - time.time() + 1))
        except ValueError:
            pass
    return 1.0


def raw_download_headers(api_headers: Optional[dict]) -> dict:
    headers = {}
    if api_headers and "Authorization" in api_headers:
        headers["Authorization"] = api_headers["Authorization"]
    return headers


def request_get(
    url: str,
    *,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    timeout: tuple = (30, 30),
    retries: int = 3,
    getter: Callable = requests.get,
    sleep: Callable[[float], None] = time.sleep,
):
    last_error = None
    attempts = max(1, retries)
    response = None
    for attempt in range(attempts):
        try:
            response = getter(url, headers=headers, params=params, timeout=timeout)
        except TRANSIENT_HTTP_ERRORS as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            sleep(min(8.0, 0.5 * (2 ** attempt)))
            continue
        if is_rate_limited(response) and attempt < attempts - 1:
            sleep(min(8.0, rate_limit_wait(response)))
            continue
        return response
    if last_error:
        raise last_error
    return response


def download_file_text(
    item: dict,
    *,
    api_headers: Optional[dict] = None,
    getter: Callable = requests.get,
    sleep: Callable[[float], None] = time.sleep,
    retries: int = 3,
    max_file_size: Optional[int] = None,
) -> Optional[str]:
    path = item.get("path") or item.get("name") or "unknown"
    download_url = item.get("download_url")
    if download_url:
        try:
            response = request_get(
                download_url,
                headers=raw_download_headers(api_headers),
                getter=getter,
                sleep=sleep,
                retries=retries,
            )
            if response.status_code == 200:
                content_length = int(response.headers.get("content-length", 0) or 0)
                if max_file_size and content_length > max_file_size:
                    raise ValueError(
                        f"Content length ({content_length} bytes) exceeds limit ({max_file_size} bytes)"
                    )
                return response.text
            print(f"Failed to download {path} from raw URL: {response.status_code}")
        except TRANSIENT_HTTP_ERRORS as exc:
            print(f"Raw download failed for {path} ({exc}); falling back to GitHub API")

    api_url = item.get("url")
    if not api_url:
        return None

    try:
        content_response = request_get(
            api_url,
            headers=api_headers,
            getter=getter,
            sleep=sleep,
            retries=retries,
        )
    except TRANSIENT_HTTP_ERRORS as exc:
        print(f"Skipping {path}: GitHub API fallback also failed ({exc})")
        return None

    if content_response.status_code != 200:
        print(f"Failed to get content for {path}: {content_response.status_code}")
        return None

    content_data = content_response.json()
    if content_data.get("encoding") == "base64" and "content" in content_data:
        estimated_size = int(len(content_data["content"]) * 0.75)
        if max_file_size and estimated_size > max_file_size:
            raise ValueError("Encoded content exceeds size limit")
        return base64.b64decode(content_data["content"]).decode("utf-8")

    print(f"Unexpected content format for {path}")
    return None


def crawl_github_files(
    repo_url, 
    token=None, 
    max_file_size: int = 1 * 1024 * 1024,  # 1 MB
    use_relative_paths: bool = False,
    include_patterns: Union[str, Set[str]] = None,
    exclude_patterns: Union[str, Set[str]] = None
):
    """
    Crawl files from a specific path in a GitHub repository at a specific commit.

    Args:
        repo_url (str): URL of the GitHub repository with specific path and commit
                        (e.g., 'https://github.com/microsoft/autogen/tree/e45a15766746d95f8cfaaa705b0371267bec812e/python/packages/autogen-core/src/autogen_core')
        token (str, optional): **GitHub personal access token.**
            - **Required for private repositories.**
            - **Recommended for public repos to avoid rate limits.**
            - Can be passed explicitly or set via the `GITHUB_TOKEN` environment variable.
        max_file_size (int, optional): Maximum file size in bytes to download (default: 1 MB)
        use_relative_paths (bool, optional): If True, file paths will be relative to the specified subdirectory
        include_patterns (str or set of str, optional): Pattern or set of patterns specifying which files to include (e.g., "*.py", {"*.md", "*.txt"}).
                                                       If None, all files are included.
        exclude_patterns (str or set of str, optional): Pattern or set of patterns specifying which files to exclude.
                                                       If None, no files are excluded.

    Returns:
        dict: Dictionary with files and statistics
    """
    # Convert single pattern to set
    if include_patterns and isinstance(include_patterns, str):
        include_patterns = {include_patterns}
    if exclude_patterns and isinstance(exclude_patterns, str):
        exclude_patterns = {exclude_patterns}

    def should_include_file(file_path: str, file_name: str) -> bool:
        """Determine if a file should be included based on patterns"""
        # If no include patterns are specified, include all files
        if not include_patterns:
            include_file = True
        else:
            # Check if file matches any include pattern
            include_file = any(fnmatch.fnmatch(file_name, pattern) for pattern in include_patterns)

        # If exclude patterns are specified, check if file should be excluded
        if exclude_patterns and include_file:
            # Exclude if file matches any exclude pattern
            exclude_file = any(fnmatch.fnmatch(file_path, pattern) for pattern in exclude_patterns)
            return not exclude_file

        return include_file

    # Detect SSH URL (git@ or .git suffix)
    is_ssh_url = repo_url.startswith("git@") or repo_url.endswith(".git")

    if is_ssh_url:
        # Clone repo via SSH to temp dir
        with tempfile.TemporaryDirectory() as tmpdirname:
            print(f"Cloning SSH repo {repo_url} to temp dir {tmpdirname} ...")
            try:
                repo = git.Repo.clone_from(repo_url, tmpdirname)
            except Exception as e:
                print(f"Error cloning repo: {e}")
                return {"files": {}, "stats": {"error": str(e)}}

            # Attempt to checkout specific commit/branch if in URL
            # Parse ref and subdir from SSH URL? SSH URLs don't have branch info embedded
            # So rely on default branch, or user can checkout manually later
            # Optionally, user can pass ref explicitly in future API

            # Walk directory
            files = {}
            skipped_files = []

            for root, dirs, filenames in os.walk(tmpdirname):
                for filename in filenames:
                    abs_path = os.path.join(root, filename)
                    rel_path = os.path.relpath(abs_path, tmpdirname)

                    # Check file size
                    try:
                        file_size = os.path.getsize(abs_path)
                    except OSError:
                        continue

                    if file_size > max_file_size:
                        skipped_files.append((rel_path, file_size))
                        print(f"Skipping {rel_path}: size {file_size} exceeds limit {max_file_size}")
                        continue

                    # Check include/exclude patterns
                    if not should_include_file(rel_path, filename):
                        print(f"Skipping {rel_path}: does not match include/exclude patterns")
                        continue

                    # Read content
                    try:
                        with open(abs_path, "r", encoding="utf-8-sig") as f:
                            content = f.read()
                        files[rel_path] = content
                        print(f"Added {rel_path} ({file_size} bytes)")
                    except Exception as e:
                        print(f"Failed to read {rel_path}: {e}")

            return {
                "files": files,
                "stats": {
                    "downloaded_count": len(files),
                    "skipped_count": len(skipped_files),
                    "skipped_files": skipped_files,
                    "base_path": None,
                    "include_patterns": include_patterns,
                    "exclude_patterns": exclude_patterns,
                    "source": "ssh_clone"
                }
            }

    # Parse GitHub URL to extract owner, repo, commit/branch, and path
    parsed_url = urlparse(repo_url)
    path_parts = parsed_url.path.strip('/').split('/')
    
    if len(path_parts) < 2:
        raise ValueError(f"Invalid GitHub URL: {repo_url}")
    
    # Extract the basic components
    owner = path_parts[0]
    repo = path_parts[1]
    
    # Setup for GitHub API
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    def fetch_branches(owner: str, repo: str):
        """Get brancshes of the repository"""

        url = f"https://api.github.com/repos/{owner}/{repo}/branches"
        response = requests.get(url, headers=headers, timeout=(30, 30))

        if response.status_code == 404:
            if not token:
                print(f"Error 404: Repository not found or is private.\n"
                      f"If this is a private repository, please provide a valid GitHub token via the 'token' argument or set the GITHUB_TOKEN environment variable.")
            else:
                print(f"Error 404: Repository not found or insufficient permissions with the provided token.\n"
                      f"Please verify the repository exists and the token has access to this repository.")
            return []
            
        if response.status_code != 200:
            print(f"Error fetching the branches of {owner}/{repo}: {response.status_code} - {response.text}")
            return []

        return response.json()

    def check_tree(owner: str, repo: str, tree: str):
        """Check the repository has the given tree"""

        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{tree}"
        response = requests.get(url, headers=headers, timeout=(30, 30))

        return True if response.status_code == 200 else False 

    # Check if URL contains a specific branch/commit
    if len(path_parts) > 2 and 'tree' == path_parts[2]:
        join_parts = lambda i: '/'.join(path_parts[i:])

        branches = fetch_branches(owner, repo)
        branch_names = map(lambda branch: branch.get("name"), branches)

        # Fetching branches is not successfully
        if len(branches) == 0:
            return

        # To check branch name
        relevant_path = join_parts(3)

        # Find a match with relevant path and get the branch name
        filter_gen = (name for name in branch_names if relevant_path.startswith(name))
        ref = next(filter_gen, None)

        # If match is not found, check for is it a tree
        if ref == None:
            tree = path_parts[3]
            ref = tree if check_tree(owner, repo, tree) else None

        # If it is neither a tree nor a branch name
        if ref == None:
            print(f"The given path does not match with any branch and any tree in the repository.\n"
                  f"Please verify the path is exists.")
            return

        # Combine all parts after the ref as the path
        part_index = 5 if '/' in ref else 4
        specific_path = join_parts(part_index) if part_index < len(path_parts) else ""
    else:
        # Dont put the ref param to quiery
        # and let Github decide default branch
        ref = None
        specific_path = ""
    
    # Dictionary to store path -> content mapping
    files = {}
    skipped_files = []
    failed_downloads = []

    base_prefix = specific_path.rstrip('/') if specific_path else ""

    def under_base(item_path: str) -> bool:
        return path_under_base(item_path, base_prefix)

    def relative_to_base(item_path: str) -> str:
        return relative_to_base_path(item_path, base_prefix, use_relative_paths)

    def default_branch():
        try:
            response = request_get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers)
        except TRANSIENT_HTTP_ERRORS:
            return None
        if response.status_code != 200:
            return None
        return response.json().get("default_branch")

    def fetch_tree(tree_ref: str):
        """List the whole repository in one call instead of one call per directory."""
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{tree_ref}"
        try:
            response = request_get(url, headers=headers, params={"recursive": "1"})
        except TRANSIENT_HTTP_ERRORS as exc:
            print(f"Tree listing failed ({exc}); falling back to per-directory crawl")
            return None
        if response.status_code != 200:
            print(
                f"Tree listing unavailable ({response.status_code}); "
                f"falling back to per-directory crawl"
            )
            return None
        payload = response.json()
        if payload.get("truncated"):
            print("Tree listing truncated by GitHub; falling back to per-directory crawl")
            return None
        return payload.get("tree", [])

    def crawl_via_tree(tree_ref: str) -> bool:
        entries = fetch_tree(tree_ref)
        if entries is None:
            return False

        wanted = []
        for entry in entries:
            if entry.get("type") != "blob":
                continue
            item_path = entry["path"]
            if not under_base(item_path):
                continue
            rel_path = relative_to_base(item_path)
            name = item_path.rsplit("/", 1)[-1]
            if not should_include_file(rel_path, name):
                continue
            file_size = entry.get("size", 0) or 0
            if file_size > max_file_size:
                skipped_files.append((item_path, file_size))
                print(
                    f"Skipping {rel_path}: File size ({file_size} bytes) exceeds limit "
                    f"({max_file_size} bytes)"
                )
                continue
            wanted.append(
                {
                    "path": item_path,
                    "name": name,
                    "rel_path": rel_path,
                    "size": file_size,
                    "url": entry.get("url"),
                    "download_url": (
                        f"https://raw.githubusercontent.com/{owner}/{repo}/{tree_ref}/"
                        f"{quote(item_path, safe='/')}"
                    ),
                }
            )

        if not wanted and base_prefix:
            # An unusual ref can resolve to a tree that does not contain the requested
            # subdirectory at all; let the slower walk have a go before giving up.
            print(f"Tree listing had nothing under '{base_prefix}'; falling back to per-directory crawl")
            return False

        workers = max(1, min(GITHUB_MAX_CONCURRENCY, len(wanted)))
        print(f"Downloading {len(wanted)} files with {workers} workers...")

        def fetch_one(item):
            try:
                return item, download_file_text(
                    item, api_headers=headers, max_file_size=max_file_size
                ), None
            except ValueError as exc:
                return item, None, exc

        if wanted:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for item, content, error in pool.map(fetch_one, wanted):
                    if error is not None:
                        skipped_files.append((item["path"], item["size"]))
                        print(f"Skipping {item['rel_path']}: {error}")
                    elif content is None:
                        failed_downloads.append(item["rel_path"])
                        print(f"Failed to download {item['rel_path']}")
                    else:
                        files[item["rel_path"]] = content
            raise_if_downloads_incomplete(failed_downloads, len(wanted))
        return True

    def fetch_contents(path):
        """Fetch contents of the repository at a specific path and commit"""
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        params = {"ref": ref} if ref != None else {}
        
        try:
            response = request_get(url, headers=headers, params=params)
        except TRANSIENT_HTTP_ERRORS as exc:
            print(f"Error fetching {path}: {exc}")
            return
        
        if response.status_code == 403 and 'rate limit exceeded' in response.text.lower():
            reset_time = int(response.headers.get('X-RateLimit-Reset', 0))
            wait_time = max(reset_time - time.time(), 0) + 1
            print(f"Rate limit exceeded. Waiting for {wait_time:.0f} seconds...")
            time.sleep(wait_time)
            return fetch_contents(path)
            
        if response.status_code == 404:
            if not token:
                print(f"Error 404: Repository not found or is private.\n"
                      f"If this is a private repository, please provide a valid GitHub token via the 'token' argument or set the GITHUB_TOKEN environment variable.")
            elif not path and ref == 'main':
                print(f"Error 404: Repository not found. Check if the default branch is not 'main'\n"
                      f"Try adding branch name to the request i.e. python main.py --repo https://github.com/username/repo/tree/master")
            else:
                print(f"Error 404: Path '{path}' not found in repository or insufficient permissions with the provided token.\n"
                      f"Please verify the token has access to this repository and the path exists.")
            return
            
        if response.status_code != 200:
            print(f"Error fetching {path}: {response.status_code} - {response.text}")
            return
        
        contents = response.json()
        
        # Handle both single file and directory responses
        if not isinstance(contents, list):
            contents = [contents]
        
        for item in contents:
            item_path = item["path"]
            
            rel_path = relative_to_base(item_path)
            
            if item["type"] == "file":
                # Check if file should be included based on patterns
                if not should_include_file(rel_path, item["name"]):
                    print(f"Skipping {rel_path}: Does not match include/exclude patterns")
                    continue
                
                # Check file size if available
                file_size = item.get("size", 0)
                if file_size > max_file_size:
                    skipped_files.append((item_path, file_size))
                    print(f"Skipping {rel_path}: File size ({file_size} bytes) exceeds limit ({max_file_size} bytes)")
                    continue
                
                try:
                    file_content = download_file_text(
                        item,
                        api_headers=headers,
                        max_file_size=max_file_size,
                    )
                except ValueError as exc:
                    skipped_files.append((item_path, file_size))
                    print(f"Skipping {rel_path}: {exc}")
                    continue

                if file_content is None:
                    failed_downloads.append(rel_path)
                    print(f"Failed to download {rel_path}")
                    continue

                files[rel_path] = file_content
                print(f"Downloaded: {rel_path} ({file_size} bytes) ")
            
            elif item["type"] == "dir":
                # OLD IMPLEMENTATION (comment this block to test new implementation)
                # Always recurse into directories without checking exclusions first
                # fetch_contents(item_path)

                # NEW IMPLEMENTATION (uncomment this block to test optimized version)
                # # Check if directory should be excluded before recursing
                if exclude_patterns:
                    dir_excluded = any(fnmatch.fnmatch(item_path, pattern) or
                                    fnmatch.fnmatch(rel_path, pattern) for pattern in exclude_patterns)
                    if dir_excluded:
                        continue
                
                # # Only recurse if directory is not excluded
                fetch_contents(item_path)
    
    # One recursive tree call plus parallel downloads; the per-directory walk below is the
    # fallback for refs the tree API cannot serve (or trees GitHub truncates).
    tree_ref = ref or default_branch()
    source = "tree"
    if not (tree_ref and crawl_via_tree(tree_ref)):
        source = "contents_walk"
        files.clear()
        skipped_files.clear()
        failed_downloads.clear()
        fetch_contents(specific_path)
        raise_if_downloads_incomplete(failed_downloads, max(len(files) + len(failed_downloads), 1))

    return {
        "files": dict(sort_files(files)),
        "stats": {
            "downloaded_count": len(files),
            "skipped_count": len(skipped_files),
            "skipped_files": skipped_files,
            "base_path": specific_path if use_relative_paths else None,
            "include_patterns": include_patterns,
            "exclude_patterns": exclude_patterns,
            "source": source,
        }
    }

# Example usage
if __name__ == "__main__":
    # Get token from environment variable (recommended for private repos)
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        print("Warning: No GitHub token found in environment variable 'GITHUB_TOKEN'.\n"
              "Private repositories will not be accessible without a token.\n"
              "To access private repos, set the environment variable or pass the token explicitly.")
    
    repo_url = "https://github.com/pydantic/pydantic/tree/6c38dc93f40a47f4d1350adca9ec0d72502e223f/pydantic"
    
    # Example: Get Python and Markdown files, but exclude test files
    result = crawl_github_files(
        repo_url, 
        token=github_token,
        max_file_size=1 * 1024 * 1024,  # 1 MB in bytes
        use_relative_paths=True,  # Enable relative paths
        include_patterns={"*.py", "*.md"},  # Include Python and Markdown files
    )
    
    files = result["files"]
    stats = result["stats"]
    
    print(f"\nDownloaded {stats['downloaded_count']} files.")
    print(f"Skipped {stats['skipped_count']} files due to size limits or patterns.")
    print(f"Base path for relative paths: {stats['base_path']}")
    print(f"Include patterns: {stats['include_patterns']}")
    print(f"Exclude patterns: {stats['exclude_patterns']}")
    
    # Display all file paths in the dictionary
    print("\nFiles in dictionary:")
    for file_path in sorted(files.keys()):
        print(f"  {file_path}")
    
    # Example: accessing content of a specific file
    if files:
        sample_file = next(iter(files))
        print(f"\nSample file: {sample_file}")
        print(f"Content preview: {files[sample_file][:200]}...")
