from maa.agent.agent_server import AgentServer

from backend.app.shared.backend.paste_text_action import PasteChatText


AgentServer.custom_action("PasteChatText")(PasteChatText)
