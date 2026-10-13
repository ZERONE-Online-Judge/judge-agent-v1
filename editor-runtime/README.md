# Editor execution runtime

The supervisor, native resource launcher, and image definitions are copied
from editor-backend-v1 at e0c347c. They preserve the existing compiler,
interactive execution, PDF renderer, and artifact wire format.

Only trusted agent orchestration receives the host Docker socket. Uploaded
code runs inside a separate network-free container without that socket,
credentials, host bind mounts, or root privileges. Images are resolved to
immutable digests at agent startup. Update these files together with the
Editor execution protocol and verify the actual isolation tests.

Run `sudo bash deploy/prepare-editor-runtime.sh` on each selected judge VM,
then recreate the idle judge-agent using its existing deployment environment
and VM CPU/memory limits. The agent advertises editor-execute-v1 only after
both images are available. A failed image build must leave the old agent
running. Build these images before deploying Editor's agent-only transport.

Editor results use the dedicated internal editor-tasks result route. The
existing JUDGE_OBJECT_URL_ORIGINS mapping and TLS CA remain in force; do not
use the public domain for internal object downloads or disable TLS checks.

The agent application image includes Debian's
[docker-cli package](https://packages.debian.org/trixie/docker-cli) and
verifies the client binary during its build. It connects to the VM's
existing Docker daemon; no second daemon is needed inside the agent.
