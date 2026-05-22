from agents import agent


while True:
    try:
        user_message = input("User: ")
        
        # Evita enviar mensajes vacíos
        if not user_message.strip():
            continue
            
        result = agent.run_sync(user_message)
        
        # Corregido: Aquí imprimes la respuesta del AGENTE
        print(f"Agent: {result.output}\n") 
        
    except (KeyboardInterrupt, EOFError):
        print("\n Bye!")
        break
