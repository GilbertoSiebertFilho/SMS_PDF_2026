"""Ponto de entrada do AgroSuite.

Sobe o servidor local e abre o navegador. Por padrão escuta apenas em
``127.0.0.1``: nenhuma outra máquina da rede alcança o app, e nenhum dado sai
do computador.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser


def find_free_port(preferred: int, host: str = "127.0.0.1") -> int:
    """Devolve ``preferred`` se estiver livre, ou a primeira porta livre acima.

    Uma sessão anterior que não encerrou direito deixa a porta ocupada; em vez
    de falhar com "endereço em uso", o app simplesmente sobe na porta seguinte.
    """
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(
        f"Nenhuma porta livre entre {preferred} e {preferred + 49}. "
        "Feche outras instâncias do AgroSuite e tente de novo."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agrosuite",
        description="Abre o AgroSuite no navegador.",
    )
    parser.add_argument("--port", type=int, default=8765, help="Porta preferida (padrão: 8765).")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Endereço de escuta. Mantenha 127.0.0.1 para uso local.")
    parser.add_argument("--no-browser", action="store_true",
                        help="Não abrir o navegador automaticamente.")
    parser.add_argument("--reload", action="store_true",
                        help="Recarregar ao alterar o código (desenvolvimento).")
    args = parser.parse_args(argv)

    try:
        import uvicorn
    except ImportError:
        print(
            "Dependências não instaladas. Rode:\n"
            "    pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    port = find_free_port(args.port, args.host)
    url = f"http://{args.host}:{port}"

    print(f"\n  AgroSuite em {url}")
    print("  Feche esta janela para encerrar o app.\n")

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        "agrosuite.app.server:app",
        host=args.host,
        port=port,
        reload=args.reload,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
