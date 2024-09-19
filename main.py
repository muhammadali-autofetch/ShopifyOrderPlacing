from flask import Flask, request, redirect, url_for, render_template, session, flash, jsonify
import csv
import requests
import json
import io
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from datetime import datetime
import time


# current dir file
current_dir = os.path.dirname(os.path.abspath(__file__))
print(current_dir)
# quit()
app = Flask(__name__)
app.secret_key = 'ali'


STORE_CONFIG_PATH = current_dir + '/JsonFiles/store_config.json'
ORDER_STATS_FILE = current_dir + '/JsonFiles/order_stats.json'

# create if doesn't exist
if not os.path.exists(current_dir + '/JsonFiles'):
    os.makedirs(current_dir + '/JsonFiles')

# Check if the store configuration file exists
if not os.path.isfile(STORE_CONFIG_PATH):
    with open(STORE_CONFIG_PATH, 'w') as f:
        json.dump({}, f)

with open(STORE_CONFIG_PATH) as f:
    STORE_CONFIG = json.load(f)

store_threads = {}  # Noqa


def get_product_and_variant_ids(store_url, api_key, password):
    """
    Function to retrieve product and variant IDs from the Shopify store
    """

    products_url = f'https://{api_key}:{password}@{store_url}/admin/api/2023-10/products.json'
    response = requests.get(products_url)
    if response.status_code == 200:
        products = response.json().get('products', [])
        product_variant_map = {}
        for product in products:
            product_id = product['id']
            product_variant_map[product_id] = [variant['id'] for variant in product['variants']]
        return product_variant_map
    else:
        print("Failed to retrieve products.")
        return {}


def read_csv_data(file):
    """
    Function to read CSV data and convert it into a list of orders
    """

    orders = []
    stream = io.TextIOWrapper(file.stream, encoding='utf-8', newline='')
    reader = csv.DictReader(stream)
    for row in reader:
        orders.append({
            'quantity': int(row['QUANTITY']),
            'product_id': int(row['SKU']),
            'first_name': row['FIRST NAME'],
            'last_name': row['LAST NAME'],
            'phone': row['PHONE'],
            'address1': row['ADDRESS 1'],
            'address2': row['ADDRESS 2'],
            'city': row['CITY'],
            'state': row['STATE'],
            'pincode': row['PINCODE'],
            'payment_status': row['PAYMENT STATUS']
        })
    return orders


def create_order(store_url, api_key, password, variant_id, customer_data, store_name):
    """
    Function to create an order on the Shopify store
    """

    orders_url = f'https://{api_key}:{password}@{store_url}/admin/api/2023-10/orders.json'
    order_data = {
        "order": {
            "line_items": [
                {
                    "variant_id": variant_id,
                    "quantity": customer_data['quantity']
                }
            ],
            "customer": {
                "first_name": customer_data['first_name'],
                "last_name": customer_data['last_name'],
                "phone": customer_data['phone']
            },
            "billing_address": {
                "first_name": customer_data['first_name'],
                "last_name": customer_data['last_name'],
                "address1": customer_data['address1'],
                "address2": customer_data['address2'],
                "city": customer_data['city'],
                "province": customer_data['state'],
                "zip": customer_data['pincode'],
                "phone": customer_data['phone']
            },
            "shipping_address": {
                "first_name": customer_data['first_name'],
                "last_name": customer_data['last_name'],
                "address1": customer_data['address1'],
                "address2": customer_data['address2'],
                "city": customer_data['city'],
                "province": customer_data['state'],
                "zip": customer_data['pincode'],
                "phone": customer_data['phone']
            },
            "financial_status": customer_data["payment_status"],
            "send_receipt": True,
            "send_fulfillment_receipt": True
        }
    }

    response = requests.post(orders_url, headers={'Content-Type': 'application/json'}, data=json.dumps(order_data))
    if response.status_code == 201:
        order_response = response.json()
        order_id = order_response.get('order', {}).get('id')
        print(f"Order {order_id} created successfully for {store_name}.")
    else:
        print(f"Failed to create order: {response.json()}")


def update_order_stats(store_name, total_orders, pending_orders, last_order_time):
    """
    Function to update order statistics
    """

    order_stats = {}

    if os.path.exists(ORDER_STATS_FILE):
        try:
            with open(ORDER_STATS_FILE, 'r') as f:
                order_stats = json.load(f)
                if not isinstance(order_stats, dict):
                    order_stats = {}
        except (json.JSONDecodeError, ValueError):
            order_stats = {}

    # Update the order stats for the store
    order_stats[store_name] = {
        "total_orders": total_orders,
        "pending_orders": pending_orders,
        "last_order_time": last_order_time,
    }

    # Write the updated stats back to the JSON file
    with open(ORDER_STATS_FILE, 'w') as f:
        json.dump(order_stats, f, indent=4)


def process_orders_in_batches(product_variant_map, orders, batch_size=1, delay=15, store_url=None, api_key=None,
                              password=None, store_name=None):
    """
    Function to process orders in batches
    """
    stop_event = store_threads.get(store_name)
    total_orders = len(orders)
    for i in range(0, total_orders, batch_size):
        if stop_event and stop_event.is_set():
            print(f"Stopping order processing for {store_name}.")
            return

        batch = orders[i:i + batch_size]  # NOQA
        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            futures = []
            for order in batch:
                product_id = order['product_id']
                if product_id in product_variant_map:
                    variant_ids = product_variant_map[product_id]
                    for variant_id in variant_ids:
                        future = executor.submit(create_order, store_url, api_key, password, variant_id, order,
                                                 store_name)
                        futures.append(future)

            for future in as_completed(futures):
                future.result()

        pending_orders = total_orders - (i + batch_size)
        last_order_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        update_order_stats(store_name, total_orders, pending_orders, last_order_time)

        if i + batch_size < total_orders:
            time.sleep(delay)


@app.route('/', methods=['GET', 'POST'])
def login():
    """
    Login route to authenticate the user
    """
    if 'user' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        token = request.form.get('token')
        if token == '12345':
            session['user'] = 'authenticated'
            return redirect(url_for('index'))
        else:
            flash("Invalid token", "error")
            return redirect(url_for('login'))
    return render_template('login.html')


@app.route('/create')
def index():
    """
    Route for the main page
    """
    if 'user' not in session:
        return redirect(url_for('login'))

    store_names = list(STORE_CONFIG.keys())
    return render_template('UploadFile.html', store_names=store_names)


@app.route('/upload', methods=['POST'])
def upload_file():
    """
    Route to upload a CSV file and process orders
    :return:
    """

    store_name = request.form.get('store')
    csv_file = request.files.get('csvFile')

    if not store_name or not csv_file:
        return "Store name and CSV file are required!", 400

    store_config = STORE_CONFIG.get(store_name)
    if not store_config:
        return "Invalid store name!", 400

    api_key = store_config.get('api_key')
    if not api_key:
        return f"API key for store '{store_name}' not found!", 400

    password = store_config['api_password']
    store_url = store_config['store_url']

    product_variant_map = get_product_and_variant_ids(store_url, api_key, password)
    if product_variant_map:
        orders = read_csv_data(csv_file)

        # Create a new event for the store
        stop_event = threading.Event()
        store_threads[store_name] = stop_event

        # Start processing orders in a separate thread
        threading.Thread(target=process_orders_in_batches,
                         args=(product_variant_map, orders, 1, 15, store_url, api_key, password, store_name)).start()

        return redirect(url_for('index'))
    else:
        return "No product variant mappings found.", 500


@app.route('/order')
def order_management():
    """
    Route for order management
    :return:
    """

    if os.path.exists(ORDER_STATS_FILE):
        with open(ORDER_STATS_FILE, 'r') as f:
            order_stats = json.load(f)
    else:
        order_stats = {}

    return render_template('Orders.html', stats=order_stats)


@app.route('/delete_store/<store_name>', methods=['POST'])
def delete_store(store_name):
    """
    Route to delete a store
    :param store_name:
    :return:
    """

    stop_event = store_threads.pop(store_name, None)
    if stop_event:
        stop_event.set()

    # Proceed with store deletion
    if os.path.exists(STORE_CONFIG_PATH):
        with open(STORE_CONFIG_PATH, 'r') as f:
            store_config = json.load(f)

        if store_name in store_config:
            del store_config[store_name]

            with open(STORE_CONFIG_PATH, 'w') as f:
                json.dump(store_config, f, indent=4)

            if os.path.exists(ORDER_STATS_FILE):
                with open(ORDER_STATS_FILE, 'r') as f:
                    order_stats = json.load(f)

                if store_name in order_stats:
                    del order_stats[store_name]

                    with open(ORDER_STATS_FILE, 'w') as f:
                        json.dump(order_stats, f, indent=4)

            return '', 204
        else:
            return 'Store not found', 404
    else:
        return 'Store configuration file not found', 500


@app.route('/logout')
def logout():
    """
    Route to log out the user
    """
    session.pop('user', None)
    return redirect(url_for('login'))



@app.route('/test')
def test():
    return jsonify({"Choo Choo": "Welcome to your Flask app 🚅"})


if __name__ == '__main__':
    app.run(debug=True, port=os.getenv("PORT", default=5000))
