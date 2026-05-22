from pyairtable import Api


ACCESS_TOKEN = "pat6RbHdPN4t76tu5.ba6cd9280ae7305533c89723eb47e40e4b4790224ce6b854e8c5a7b16a15e208"
BASE_ID = "appTwuay9eWslDDcs"
TABLE_NAME = "Inventarios"

api = Api(ACCESS_TOKEN)
table = api.table(BASE_ID, TABLE_NAME)

def get_table():
    print("--- getting table info ---")
    records = table.all()
    for record in records:

        print(record['fields'])
    return records



