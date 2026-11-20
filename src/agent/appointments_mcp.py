"""MCP stdio process for the demo company. Run in the MCP SDK environment."""

import os

from mcp.server.fastmcp import FastMCP

from src.services.appointments import AppointmentBook


def create_server(book: AppointmentBook) -> FastMCP:
    server = FastMCP("КлиматПлюс · Заявки")

    @server.tool()
    def available_slots() -> list[str]:
        """Найти свободные окна для визита. Время местное, часовой пояс Минска."""
        return book.available()

    @server.tool()
    def create_request(slot: str, need: str, request_id: str = "") -> dict:
        """По согласию абонента создать заявку на выбранное окно. request_id — ID звонка для безопасного повтора."""
        return book.create(slot, need, request_id=request_id or None)

    return server


if __name__ == "__main__":
    book = AppointmentBook(os.environ["APPOINTMENTS_DB"], os.environ["APPOINTMENTS_PROFILE"])
    create_server(book).run(transport="stdio")
