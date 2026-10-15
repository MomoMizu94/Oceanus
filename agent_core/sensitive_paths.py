from pathlib import Path


PROTECTED_DIRS = {
    ".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker"
}
PROTECTED_DIR_PATHS = (
    (".config", "gcloud"),
    (".local", "share", "oceanus", "sessions"),
    (".local", "share", "oceanus", "outputs")
)
PROT_FNAMES = {
    ".envrc", ".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials",
    "credentials.json", "service-account.json", "service_account.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "id_ecdsa_sk", "id_ed25519_sk"
}
KEY_CONTAINER_SUFFIXES = {
    ".pem", ".key", ".p12", ".pfx"
}


def sensitive_path_reason(path: Path) -> str | None:
    """ Return a reason to protect this path without reading it. If not problems -> return None """
    # Split the path into components
    parts = tuple(part.casefold() for part in path.parts)
    name = path.name.casefold()

    for part in parts:
        if part in PROTECTED_DIRS:
            return "Path is inside a protected credential or saved-data directory."
        
    for protected in PROTECTED_DIR_PATHS:
        width = len(protected)
        # Check for consecutive components
        for start in range(len(parts) - width + 1):
            if parts[start:start + width] == protected:
                return "Path is inside a protected credential or saved-data directory."

    if name == ".env" or name.startswith(".env."):
        return "Environment files may contain secrets, including example files."

    if name in PROT_FNAMES:
        return "Filename is commonly used for credentials or private keys."

    if path.suffix.casefold() in KEY_CONTAINER_SUFFIXES:
        return "File may contain a private key or credential bundle."

    return None