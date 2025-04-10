import faker
import random
import datetime
import uuid
import json  # For formatting properties in Cypher

# --- Configuration ---
OUTPUT_CYPHER_FILE = "retail_data_generation.cypher"

# Volume Estimates (Adjust as needed for testing/performance)
NUM_CUSTOMERS = 5000  # Reduced for faster testing, scale up later (original: 50,000)
NUM_PRODUCTS = 1000   # Reduced (original: 10,000)
NUM_STORES = 50     # Reduced (original: 150)
NUM_SUPPLIERS = 100   # Reduced (original: 300)
NUM_ORDERS = 20000  # Reduced (original: 200,000)
NUM_SALES_TRANSACTIONS = 25000 # Reduced (original: 250,000) - Must be >= NUM_ORDERS
NUM_INTERACTIONS_SESSIONS = 100000 # Reduced (original: 2,000,000)
# Event numbers will be derived from interactions/transactions

# USA Focus
COUNTRY_CODE = "US"
COUNTRY_NAME = "United States"
CURRENCY_CODE = "USD"
LEGAL_ENTITY_COUNTRY = "USA"

# Timestamps & Dates
DATA_START_DATE = datetime.date(2022, 1, 1)
DATA_END_DATE = datetime.date(2024, 6, 30)

# Controlled Vocabularies (Simplified examples)
PRODUCT_CATEGORIES = ["Womenswear", "Menswear", "Kidswear", "Footwear", "Accessories"]
PRODUCT_SUB_CATEGORIES = {
    "Womenswear": ["Dresses", "Tops", "Bottoms", "Outerwear"],
    "Menswear": ["Shirts", "Trousers", "Suits", "Jackets"],
    "Kidswear": ["Baby", "Girls", "Boys"],
    "Footwear": ["Sneakers", "Boots", "Sandals", "Formal"],
    "Accessories": ["Bags", "Jewelry", "Belts", "Scarves"]
}
COLORS = ["Red", "Blue", "Green", "Black", "White", "Grey", "Pink", "Yellow", "Purple", "Brown", "Orange", "Beige"]
SIZES = ["XS", "S", "M", "L", "XL", "XXL", "One Size"]
BRANDS = ["UrbanThreads", "ClassicStyle Co.", "EcoWear", "SportFlex", "Glamourama", "KidzJoy"]
PROMOTION_TYPES = ["Percentage Off", "Fixed Amount Off", "BOGO", "Free Shipping"]
ORDER_STATUSES = ["Pending", "Processing", "Shipped", "Delivered", "Cancelled", "Returned"]
CHANNEL_TYPES = ["Web", "MobileApp", "Store", "Social", "CallCenter"]
LOYALTY_TIERS = ["Bronze", "Silver", "Gold", "Platinum"]
RETURN_REASONS = ["Wrong Size", "Didn't Like Style", "Defective Item", "Changed Mind", "Received Wrong Item"]
LIFECYCLE_STAGES = ["Introduction", "Growth", "Maturity", "Decline"]
SEASONS = ["SS22", "FW22", "SS23", "FW23", "SS24", "FW24"]
SUPPLIER_TIERS = ["Tier 1 Strategic", "Tier 2 Preferred", "Tier 3 Standard"]
SUPPLIER_TYPES = ["Raw Material", "Finished Goods", "Services", "Logistics"]
PAYMENT_METHODS = ["CreditCard", "DebitCard", "PayPal", "StoreCredit", "GiftCard"]
DELIVERY_METHODS = ["Standard Shipping", "Express Shipping", "Next Day Air", "Click & Collect"]
CARRIERS = ["UPS", "FedEx", "USPS", "DHL", "Local Courier"]

# Hypothesis Biasing Parameters
H1_LOYALTY_CONVERSION_MULTIPLIER = 3.0  # Loyalty promo more likely to convert
H2_RECOMMENDATION_ADD_TO_CART_MULTIPLIER = 1.8
H2_RECOMMENDATION_CONVERSION_MULTIPLIER = 2.5
H3_VIZ_SCORE_ADD_TO_CART_MULTIPLIER = 1.5 # Add-to-cart higher for high viz score
H3_VIZ_SCORE_CONVERSION_MULTIPLIER = 1.3 # Conversion higher for high viz score
H4_SOCIAL_AOV_MULTIPLIER = 1.25        # Higher AOV for social channel journeys
H5_RECOVERY_RATE_WITH_FOLLOWUP = 0.70 # 70% recovery if follow-up interaction
H5_RECOVERY_RATE_NO_FOLLOWUP = 0.10   # 10% recovery otherwise
HIGH_VIZ_SCORE_THRESHOLD = 0.8
HIGH_VIZ_PRODUCT_PERCENTAGE = 0.20
LOYALTY_CUSTOMER_PERCENTAGE = 0.30
SESSIONS_WITH_RECOMMENDATION_PERCENTAGE = 0.30
SOCIAL_JOURNEY_PERCENTAGE = 0.15
CART_ABANDONMENT_RATE = 0.40 # % of sessions with AddToCart but no purchase
FOLLOWUP_AFTER_ABANDONMENT_PERCENTAGE = 0.60

# --- Initialize Faker ---
fake = faker.Faker('en_US')

# --- Global Storage for Linking ---
# Store generated data (IDs and key attributes) to create relationships later
generated_data = {
    "customers": [],
    "products": [],
    "stores": [],
    "suppliers": [],
    "promotions": [],
    "campaigns": [],
    "channels": [],
    "loyalty_program": None,
    "loyalty_tiers": [],
    "loyalty_segment": None,
    "orders": [],
    "transactions": [],
    "web_sessions": [],
    "search_terms": set(),
    "abandoned_carts": [], # Store { "session_id": ..., "customer_id": ..., "products": [...] }
    "social_journey_customers": set(), # Store customer IDs who interacted via Social
}

# --- Helper Functions ---

def generate_unique_id(prefix=""):
    """Generates a unique ID."""
    return f"{prefix}{uuid.uuid4()}"

def format_cypher_properties(props):
    """Formats a dictionary of properties for Cypher CREATE/MERGE SET clause."""
    items = []
    for key, value in props.items():
        if value is None:
            continue # Don't include null properties unless explicitly needed
        if isinstance(value, str):
            # Escape backslashes and single quotes
            escaped_value = value.replace('\\', '\\\\').replace("'", "\\'")
            items.append(f"{key}: '{escaped_value}'")
        elif isinstance(value, bool):
            items.append(f"{key}: {str(value).lower()}") # true/false
        elif isinstance(value, (int, float)):
            items.append(f"{key}: {value}")
        elif isinstance(value, datetime.datetime):
             # Ensure datetime is timezone-aware or use Neo4j temporal functions
             # For simplicity, using ISO format string. Adjust if timezone needed.
             # Example: Z indicates UTC. Adjust if using local times without timezone info.
            dt_str = value.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
            items.append(f"{key}: datetime('{dt_str}')")
        elif isinstance(value, datetime.date):
            items.append(f"{key}: date('{value.isoformat()}')")
        # Add other types like lists if needed, e.g., f"{key}: {json.dumps(value)}"
    return f"{{{', '.join(items)}}}"

def write_cypher(f, query):
    """Writes a Cypher query to the file."""
    f.write(query + ";\n")

def get_random_date(start=DATA_START_DATE, end=DATA_END_DATE):
    """Generates a random date between start and end."""
    return fake.date_between_dates(date_start=start, date_end=end)

def get_random_datetime(start_date=DATA_START_DATE, end_date=DATA_END_DATE):
    """Generates a random datetime between start and end dates."""
    start_dt = datetime.datetime.combine(start_date, datetime.time.min)
    end_dt = datetime.datetime.combine(end_date, datetime.time.max)
    return fake.date_time_between_dates(datetime_start=start_dt, datetime_end=end_dt)

def biased_boolean(true_probability):
    """Returns True with the given probability."""
    return random.random() < true_probability

# --- Generation Functions ---

def generate_foundational_nodes(f):
    """Generates foundational nodes like LegalEntity, Country, StoreFormat, etc."""
    print("Generating foundational nodes...")

    # Countries
    write_cypher(f, f"CREATE (:Country {format_cypher_properties({'countryCode': COUNTRY_CODE, 'countryName': COUNTRY_NAME})})")
    write_cypher(f, f"CREATE (:FiscalCountry {format_cypher_properties({'fiscalCountryCode': COUNTRY_CODE, 'fiscalCountryName': COUNTRY_NAME})})")

    # Legal Entity (Internal)
    internal_legal_entity_id = generate_unique_id("le-")
    props = {
        "legalEntityID": internal_legal_entity_id,
        "isInternal": True,
        "countryOfRegistration": LEGAL_ENTITY_COUNTRY,
        "legalEntityName": fake.company() + " Holdings Inc.",
        "registeredAddress": fake.address(),
        "taxID": fake.ssn(), # Using SSN format as placeholder for Tax ID
        "incorporationDate": get_random_date(end=DATA_START_DATE),
        "entityType": "Corporation"
    }
    write_cypher(f, f"CREATE (:LegalEntity {format_cypher_properties(props)})")

    # Store Formats, Regions, Divisions (example)
    store_formats = ["Flagship", "Mall", "Outlet", "Urban Boutique"]
    regions = ["West", "Midwest", "South", "Northeast"]
    divisions = ["Luxury", "Mainstream", "Value"]
    for sf in store_formats:
        write_cypher(f, f"CREATE (:StoreFormat {format_cypher_properties({'storeFormatCode': sf.upper().replace(' ', ''), 'storeFormatName': sf})})")
    for r in regions:
        write_cypher(f, f"CREATE (:Region {format_cypher_properties({'regionCode': r.upper(), 'regionName': r})})")
    for d in divisions:
        write_cypher(f, f"CREATE (:Division {format_cypher_properties({'divisionCode': d.upper(), 'divisionName': d})})")

    # Channels
    for ch in CHANNEL_TYPES:
        channel_id = generate_unique_id("chan-")
        props = {"channelID": channel_id, "channelCode": ch, "channelType": ch}
        generated_data["channels"].append(props)
        write_cypher(f, f"CREATE (:Channel:`{ch}Channel` {format_cypher_properties(props)})")

    # Loyalty Program & Tiers
    lp_id = generate_unique_id("lp-")
    generated_data["loyalty_program"] = {"loyaltyProgramID": lp_id, "programName": "FashionRewards"}
    write_cypher(f, f"CREATE (:LoyaltyProgram {format_cypher_properties(generated_data['loyalty_program'])})")
    for i, tier_name in enumerate(LOYALTY_TIERS):
        tier_id = generate_unique_id("lt-")
        tier_props = {"loyaltyTierID": tier_id, "tierName": tier_name, "tierLevel": i + 1, "minSpend": i * 250}
        generated_data["loyalty_tiers"].append(tier_props)
        write_cypher(f, f"CREATE (:LoyaltyTier {format_cypher_properties(tier_props)})")
        # Link Tier to Program
        write_cypher(f, f"MATCH (lp:LoyaltyProgram {{loyaltyProgramID: '{lp_id}'}}), (lt:LoyaltyTier {{loyaltyTierID: '{tier_id}'}}) CREATE (lp)-[:HAS_TIER]->(lt)")

    # Loyalty Customer Segment (for H1)
    segment_id = generate_unique_id("seg-")
    generated_data["loyalty_segment"] = {"segmentID": segment_id, "segmentName": "Loyalty Members", "segmentType": "Loyalty"}
    # Note: Creating :LoyaltyCustomer label directly on Customer nodes later might be simpler
    # Or create a :CustomerSegment and link customers. Let's create the segment node.
    write_cypher(f, f"CREATE (:CustomerSegment:LoyaltyCustomerSegment {format_cypher_properties(generated_data['loyalty_segment'])})")

    # Time Hierarchy (Simplified - just years and peak periods)
    for year in range(DATA_START_DATE.year, DATA_END_DATE.year + 1):
         write_cypher(f, f"MERGE (y:Year {{year: {year}}})")
    # Example Peak Period
    peak_id = generate_unique_id("tp-")
    write_cypher(f, f"CREATE (:TimePeriodType:PeakPeriod {{timePeriodID: '{peak_id}', periodName: 'Holiday Season 2023', startDate: date('2023-11-15'), endDate: date('2023-12-31'), isPeakPeriod: true}})")
    nonpeak_id = generate_unique_id("tp-")
    write_cypher(f, f"CREATE (:TimePeriodType:NonPeakPeriod {{timePeriodID: '{nonpeak_id}', periodName: 'General Non-Peak', isPeakPeriod: false}})") # Add date range if needed

    # Brands, Colors, Sizes, Materials etc. (Nodes or properties?)
    # Let's create Brand nodes, others can be properties for simplicity now
    for brand_name in BRANDS:
         write_cypher(f, f"CREATE (:Brand {format_cypher_properties({'brandID': generate_unique_id('br-'), 'brandName': brand_name})})")


def generate_stores(f):
    """Generates Store nodes."""
    print(f"Generating {NUM_STORES} stores...")
    regions = [r['regionName'] for r in fake.provider('faker.providers.geo').pyiterable(lambda: {'regionName': fake.state()})] # Simplified region
    regions = list(set(regions)) # Get unique states as regions

    for i in range(NUM_STORES):
        store_id = generate_unique_id("st-")
        props = {
            "storeID": store_id,
            "storeName": f"FashionHub {fake.city()}",
            "storeLocation": fake.street_address(), # Simplified location
            "city": fake.city(),
            "state": fake.state_abbr(),
            "zipcode": fake.zipcode(),
            "storeManager": fake.name(),
            "storeOpeningDate": get_random_date(end=DATA_START_DATE),
            "storeSize": round(random.uniform(2000.0, 30000.0), 2),
            "storeFormat": random.choice(["Flagship", "Mall", "Outlet", "Urban Boutique"]), # Link later
            "region": random.choice(regions) # Link later
        }
        generated_data["stores"].append(props)
        write_cypher(f, f"CREATE (:Store:PhysicalStoreChannel {format_cypher_properties(props)})")
        # Link to Format, Region (Example)
        write_cypher(f, f"MATCH (s:Store {{storeID: '{store_id}'}}), (sf:StoreFormat {{storeFormatName: '{props['storeFormat']}'}}) MERGE (s)-[:HAS_FORMAT]->(sf)")
        # Find or create region node (simplistic matching)
        write_cypher(f, f"MATCH (s:Store {{storeID: '{store_id}'}}) MERGE (r:Region {{regionName: '{props['region']}'}}) MERGE (s)-[:LOCATED_IN_REGION]->(r)")
        # Link Store as a Channel
        web_channel = next(c for c in generated_data["channels"] if c['channelCode'] == 'Store')
        write_cypher(f, f"MATCH (s:Store {{storeID: '{store_id}'}}), (c:Channel {{channelID: '{web_channel['channelID']}'}}) MERGE (s)-[:IS_A]->(c)")


def generate_suppliers(f):
    """Generates Supplier and related nodes."""
    print(f"Generating {NUM_SUPPLIERS} suppliers...")
    for i in range(NUM_SUPPLIERS):
        supplier_id = generate_unique_id("sup-")
        is_manufacturer = random.random() < 0.2 # 20% are also manufacturers
        supplier_name = fake.company()
        props = {
            "supplierID": supplier_id,
            "supplierName": supplier_name,
            "supplierAddress": fake.address(),
            "supplierContactName": fake.name(),
            "supplierContactEmail": fake.email(),
            "supplierContactPhone": fake.phone_number(),
            "supplierPerformanceScore": round(random.normalvariate(0.8, 0.1), 2),
            "supplierTier": random.choice(SUPPLIER_TIERS),
            "supplierType": random.choice(SUPPLIER_TYPES)
        }
        generated_data["suppliers"].append(props)
        # Create Supplier and SupplierEntity (can be merged or separate as needed)
        node_labels = ":Supplier:SupplierEntity"
        if is_manufacturer:
            node_labels += ":Manufacturer"
            props["manufacturerCode"] = generate_unique_id("mfr-")

        write_cypher(f, f"CREATE ({node_labels} {format_cypher_properties(props)})")


def generate_products(f):
    """Generates Product nodes and related characteristics."""
    print(f"Generating {NUM_PRODUCTS} products...")
    brands = [b['brandName'] for b in fake.provider('faker.providers.misc').pyiterable(lambda: {'brandName': fake.word()})] # Placeholder for actual Brand nodes
    brands = BRANDS # Use defined brands

    high_viz_count = int(NUM_PRODUCTS * HIGH_VIZ_PRODUCT_PERCENTAGE)
    product_indices = list(range(NUM_PRODUCTS))
    random.shuffle(product_indices)
    high_viz_indices = set(product_indices[:high_viz_count])

    for i in range(NUM_PRODUCTS):
        product_id = generate_unique_id("prod-")
        sku = generate_unique_id("sku-")
        category = random.choice(PRODUCT_CATEGORIES)
        sub_category = random.choice(PRODUCT_SUB_CATEGORIES[category])
        base_price = round(random.uniform(15.0, 500.0), 2)
        cost = round(base_price * random.uniform(0.3, 0.6), 2) # Cost is 30-60% of price
        launch_date = get_random_date(end=DATA_END_DATE)
        # H3 Support: Assign visualization score
        viz_score = round(random.uniform(0.85, 1.0), 2) if i in high_viz_indices else round(random.uniform(0.1, 0.75), 2)

        props = {
            "productID": product_id,
            "productSKU": sku,
            "productBarcode": fake.ean13(),
            "productName": f"{random.choice(COLORS)} {sub_category} ({random.choice(SIZES)})",
            "productDescription": fake.sentence(nb_words=15),
            "productCategory": category,
            "productSubCategory": sub_category,
            "productBrand": random.choice(brands), # Link later
            "productColor": random.choice(COLORS),
            "productSize": random.choice(SIZES),
            "productMaterial": random.choice(["Cotton", "Polyester", "Silk", "Wool", "Leather", "Synthetic"]),
            "productWeight": round(random.uniform(0.1, 2.0), 2), # kg
            "uom": "Each",
            "isActive": biased_boolean(0.95), # 95% active
            "isCatchweight": False,
            "isTransformable": False,
            "launchDate": launch_date,
            "productLifecycle": random.choice(LIFECYCLE_STAGES),
            "productPrice": base_price, # Base price stored here for simplicity
            "productCost": cost,
            "season": random.choice(SEASONS),
            "visualizationScore": viz_score # H3 Support
        }
        generated_data["products"].append(props)
        write_cypher(f, f"CREATE (:Product:SimpleProduct {format_cypher_properties(props)})")
        # Link Product to Brand (Example)
        write_cypher(f, f"MATCH (p:Product {{productID: '{product_id}'}}), (b:Brand {{brandName: '{props['productBrand']}'}}) MERGE (p)-[:HAS_BRAND]->(b)")
        # Create Price node (optional, could be just properties on Product)
        price_id = generate_unique_id("price-")
        price_props = {"priceID": price_id, "basePrice": base_price, "currency": CURRENCY_CODE}
        write_cypher(f, f"CREATE (:Price {format_cypher_properties(price_props)})")
        write_cypher(f, f"MATCH (p:Product {{productID: '{product_id}'}}), (pr:Price {{priceID: '{price_id}'}}) CREATE (p)-[:HAS_PRICE]->(pr)")


def generate_customers(f):
    """Generates Customer nodes and related segments."""
    print(f"Generating {NUM_CUSTOMERS} customers...")
    loyalty_segment_id = generated_data["loyalty_segment"]["segmentID"]
    loyalty_program_id = generated_data["loyalty_program"]["loyaltyProgramID"]
    num_loyalty_customers = int(NUM_CUSTOMERS * LOYALTY_CUSTOMER_PERCENTAGE)
    customer_indices = list(range(NUM_CUSTOMERS))
    random.shuffle(customer_indices)
    loyalty_indices = set(customer_indices[:num_loyalty_customers])

    for i in range(NUM_CUSTOMERS):
        customer_id = generate_unique_id("cust-")
        is_loyalty = i in loyalty_indices
        acquisition_date = get_random_date(end=DATA_END_DATE)

        props = {
            "customerID": customer_id,
            "firstName": fake.first_name(),
            "lastName": fake.last_name(),
            "email": fake.unique.email(),
            "phoneNumber": fake.phone_number(),
            "address": fake.street_address(),
            "city": fake.city(),
            "state": fake.state_abbr(),
            "zipcode": fake.zipcode(),
            "country": COUNTRY_CODE,
            "customerAcquisitionDate": acquisition_date,
            "isLoyaltyMember": is_loyalty, # Simple flag
            # Demographics (example - often stored separately or derived)
            "gender": random.choice(["Male", "Female", "Non-Binary", "Prefer not to say"]),
            "birthDate": fake.date_of_birth(minimum_age=16, maximum_age=80),
        }
        # Store relevant info for relationships
        generated_data["customers"].append({
            "customerID": customer_id,
            "isLoyaltyMember": is_loyalty,
            "acquisitionDate": acquisition_date
            })

        # Add LoyaltyCustomer label for H1 targeting ease
        customer_labels = ":Customer"
        if is_loyalty:
            customer_labels += ":LoyaltyCustomer"

        write_cypher(f, f"CREATE ({customer_labels} {format_cypher_properties(props)})")

        # Link Loyalty Customers
        if is_loyalty:
            loyalty_tier = random.choice(generated_data["loyalty_tiers"])
            loyalty_tier_id = loyalty_tier["loyaltyTierID"]
            # Link to Segment
            write_cypher(f, f"MATCH (c:Customer {{customerID: '{customer_id}'}}), (ls:CustomerSegment {{segmentID: '{loyalty_segment_id}'}}) CREATE (c)-[:BELONGS_TO_SEGMENT]->(ls)")
            # Link to Loyalty Program and Tier
            write_cypher(f, f"MATCH (c:Customer {{customerID: '{customer_id}'}}), (lp:LoyaltyProgram {{loyaltyProgramID: '{loyalty_program_id}'}}) CREATE (c)-[:ENROLLED_IN]->(lp)")
            write_cypher(f, f"MATCH (c:Customer {{customerID: '{customer_id}'}}), (lt:LoyaltyTier {{loyaltyTierID: '{loyalty_tier_id}'}}) CREATE (c)-[:HAS_LOYALTY_TIER]->(lt)")


def generate_promotions_campaigns(f):
    """Generates Campaigns and Promotions, supporting H1."""
    print("Generating campaigns and promotions...")
    num_campaigns = 50 # Define number of campaigns
    num_promotions = 200 # Define number of promotions
    loyalty_segment_id = generated_data["loyalty_segment"]["segmentID"]

    # Campaigns
    for i in range(num_campaigns):
        campaign_id = generate_unique_id("camp-")
        start_date = get_random_date(end=DATA_END_DATE - datetime.timedelta(days=30))
        end_date = get_random_date(start=start_date + datetime.timedelta(days=14), end=DATA_END_DATE)
        props = {
            "campaignID": campaign_id,
            "campaignName": f"{random.choice(SEASONS)} {random.choice(['Sale', 'Launch', 'Awareness'])} Campaign",
            "campaignStartDate": start_date,
            "campaignEndDate": end_date,
            "objective": random.choice(["Increase Sales", "Drive Traffic", "Brand Awareness", "Loyalty Engagement"])
        }
        generated_data["campaigns"].append(props)
        write_cypher(f, f"CREATE (:Campaign {format_cypher_properties(props)})")

    # Promotions
    num_targeted_promotions = int(num_promotions * 0.2) # 20% targeted at loyalty members for H1
    promotion_indices = list(range(num_promotions))
    random.shuffle(promotion_indices)
    targeted_indices = set(promotion_indices[:num_targeted_promotions])

    for i in range(num_promotions):
        promo_id = generate_unique_id("promo-")
        is_targeted_loyalty = i in targeted_indices
        promo_type = random.choice(PROMOTION_TYPES)
        discount = round(random.uniform(0.05, 0.5), 2) if promo_type == "Percentage Off" else round(random.uniform(5.0, 50.0), 2)
        campaign = random.choice(generated_data["campaigns"])
        start_date = get_random_date(start=campaign['campaignStartDate'], end=campaign['campaignEndDate'] - datetime.timedelta(days=1))
        end_date = get_random_date(start=start_date + datetime.timedelta(days=7), end=campaign['campaignEndDate'])

        props = {
            "promotionID": promo_id,
            "promotionName": f"{int(discount*100)}% Off {random.choice(PRODUCT_CATEGORIES)}" if promo_type == "Percentage Off" else f"${int(discount)} Off Order",
            "promotionType": promo_type,
            "discountPercentage": discount if promo_type == "Percentage Off" else None,
            "discountAmount": discount if promo_type == "Fixed Amount Off" else None,
            "promotionStartDate": start_date,
            "promotionEndDate": end_date,
            "promoCode": generate_unique_id("CODE-").upper()[:8] if random.random() < 0.7 else None, # Optional code
            "isTargetedLoyalty": is_targeted_loyalty # Store for biasing logic
        }
        generated_data["promotions"].append(props)
        write_cypher(f, f"CREATE (:Promotion {format_cypher_properties(props)})")
        # Link to Campaign
        write_cypher(f, f"MATCH (p:Promotion {{promotionID: '{promo_id}'}}), (c:Campaign {{campaignID: '{campaign['campaignID']}'}}) CREATE (p)-[:PART_OF_CAMPAIGN]->(c)")

        # H1 Support: Link targeted promotions to Loyalty Segment via TargetAudience
        if is_targeted_loyalty:
            audience_id = generate_unique_id("aud-")
            audience_props = {"audienceID": audience_id, "audienceName": f"Targeted Loyalty - {props['promotionName']}"}
            write_cypher(f, f"CREATE (:TargetAudience {format_cypher_properties(audience_props)})")
            write_cypher(f, f"MATCH (p:Promotion {{promotionID: '{promo_id}'}}), (ta:TargetAudience {{audienceID: '{audience_id}'}}) CREATE (p)-[:HAS_TARGET_AUDIENCE]->(ta)")
            write_cypher(f, f"MATCH (ta:TargetAudience {{audienceID: '{audience_id}'}}), (ls:CustomerSegment {{segmentID: '{loyalty_segment_id}'}}) CREATE (ta)-[:TARGETS_SEGMENT]->(ls)")


def generate_interactions_and_sessions(f):
    """Generates Customer Interactions and Web Sessions, supporting H2, H3, H4, H5."""
    print(f"Generating {NUM_INTERACTIONS_SESSIONS} interactions/sessions...")
    customers = generated_data["customers"]
    products = generated_data["products"]
    channels = generated_data["channels"]
    web_channel = next(c for c in channels if c['channelCode'] == 'Web')
    mobile_channel = next(c for c in channels if c['channelCode'] == 'MobileApp')
    social_channel = next(c for c in channels if c['channelCode'] == 'Social')
    # store_channel = next(c for c in channels if c['channelCode'] == 'Store') # Interactions linked via Transaction later

    interaction_count = 0
    session_count = 0

    # Simulate interactions over time
    while interaction_count < NUM_INTERACTIONS_SESSIONS and session_count < NUM_INTERACTIONS_SESSIONS * 0.8: # Assume some non-session interactions
        customer = random.choice(customers)
        customer_id = customer["customerID"]
        channel = random.choice(channels)
        channel_id = channel["channelID"]
        interaction_time = get_random_datetime(start_date=customer['acquisitionDate'])

        # H4 Support: Flag customers interacting via Social
        if channel['channelCode'] == 'Social':
            generated_data["social_journey_customers"].add(customer_id)

        # Simulate a Web/Mobile Session
        if channel['channelCode'] in ['Web', 'MobileApp']:
            session_id = generate_unique_id("sess-")
            session_start_time = interaction_time
            session_interactions = []
            num_session_actions = random.randint(2, 25)
            session_products_viewed = []
            session_products_added_to_cart = []
            session_has_recommendation = biased_boolean(SESSIONS_WITH_RECOMMENDATION_PERCENTAGE) # H2
            session_ended_with_purchase = False # Track if purchase occurs in this session simulation
            session_search_terms = []

            # H2 Support: Create recommendation interaction if applicable
            if session_has_recommendation:
                rec_interaction_id = generate_unique_id("int-")
                rec_time = session_start_time + datetime.timedelta(seconds=random.randint(5, 60))
                rec_props = {
                    "interactionID": rec_interaction_id,
                    "interactionTimestamp": rec_time,
                    "interactionType": "PersonalizedRecommendation",
                    "channelID": channel_id, # Link interaction to channel
                    "customerID": customer_id # Link interaction to customer
                }
                # CREATE Interaction node and link to Customer/Channel
                write_cypher(f, f"""
                    MATCH (c:Customer {{customerID: '{customer_id}'}}), (ch:Channel {{channelID: '{channel_id}'}})
                    CREATE (interaction:CustomerInteraction:PersonalizedRecommendation {format_cypher_properties(rec_props)})
                    CREATE (c)-[:HAD_INTERACTION {{timestamp: interaction.interactionTimestamp}}]->(interaction)
                    CREATE (interaction)-[:OCCURRED_VIA_CHANNEL]->(ch)
                """)
                session_interactions.append({"interactionID": rec_interaction_id, "timestamp": rec_time})
                interaction_count += 1


            current_time = session_start_time
            for _ in range(num_session_actions):
                if interaction_count >= NUM_INTERACTIONS_SESSIONS: break
                current_time += datetime.timedelta(seconds=random.randint(10, 180))
                action_type = random.choice(["ProductViewedEvent", "SearchInteraction", "AddToCartEvent", "PageView", "Other"])
                interaction_id = generate_unique_id("int-")
                interaction_props = {
                    "interactionID": interaction_id,
                    "interactionTimestamp": current_time,
                    "channelID": channel_id,
                    "customerID": customer_id,
                    "sessionID": session_id # Link interaction to session
                }

                # Create base CustomerInteraction node first
                write_cypher(f, f"CREATE (interaction:CustomerInteraction {format_cypher_properties(interaction_props)})")
                # Link Interaction to Customer and Channel
                write_cypher(f, f"""
                    MATCH (c:Customer {{customerID: '{customer_id}'}}), (ch:Channel {{channelID: '{channel_id}'}}), (interaction:CustomerInteraction {{interactionID: '{interaction_id}'}})
                    CREATE (c)-[:HAD_INTERACTION {{timestamp: interaction.interactionTimestamp}}]->(interaction)
                    CREATE (interaction)-[:OCCURRED_VIA_CHANNEL]->(ch)
                 """)


                if action_type == "ProductViewedEvent" and products:
                    product = random.choice(products)
                    product_id = product["productID"]
                    viz_score = product["visualizationScore"] # H3
                    session_products_viewed.append({"productID": product_id, "viz_score": viz_score, "interactionID": interaction_id})

                    # Set specific label and link to product
                    write_cypher(f, f"""
                        MATCH (interaction:CustomerInteraction {{interactionID: '{interaction_id}'}}), (p:Product {{productID: '{product_id}'}})
                        SET interaction:ProductViewedEvent
                        CREATE (interaction)-[:VIEWED_PRODUCT]->(p)
                    """)
                    interaction_count += 1

                elif action_type == "SearchInteraction":
                    term = fake.word() if not generated_data["search_terms"] else random.choice(list(generated_data["search_terms"]) + [fake.word()] * 5)
                    generated_data["search_terms"].add(term)
                    session_search_terms.append(term)
                    search_term_node_props = {"term": term}
                    interaction_props["searchTerm"] = term # Add search term to interaction

                    # Set label, create/merge SearchTerm node and link
                    write_cypher(f, f"""
                        MATCH (interaction:CustomerInteraction {{interactionID: '{interaction_id}'}})
                        SET interaction:SearchInteraction
                        MERGE (st:SearchTerm {format_cypher_properties(search_term_node_props)})
                        CREATE (interaction)-[:USED_SEARCH_TERM]->(st)
                    """)
                    interaction_count += 1

                elif action_type == "AddToCartEvent" and session_products_viewed:
                    # Bias AddToCart based on H2 (recommendation) and H3 (viz score)
                    viewed_product_info = random.choice(session_products_viewed) # Choose a previously viewed product
                    product_id_to_add = viewed_product_info["productID"]
                    viz_score = viewed_product_info["viz_score"]

                    base_add_prob = 0.3 # Base probability to add viewed item
                    if session_has_recommendation: # H2 Boost
                        base_add_prob *= H2_RECOMMENDATION_ADD_TO_CART_MULTIPLIER
                    if viz_score >= HIGH_VIZ_SCORE_THRESHOLD: # H3 Boost
                         base_add_prob *= H3_VIZ_SCORE_ADD_TO_CART_MULTIPLIER

                    if biased_boolean(min(base_add_prob, 0.95)): # Cap probability
                        session_products_added_to_cart.append({"productID": product_id_to_add})
                        interaction_props["quantity"] = 1 # Assume adding 1 item

                        # Set label and link to Product
                        write_cypher(f, f"""
                            MATCH (interaction:CustomerInteraction {{interactionID: '{interaction_id}'}}), (p:Product {{productID: '{product_id_to_add}'}})
                            SET interaction:AddToCartEvent
                            SET interaction.quantity = 1
                            CREATE (interaction)-[:ADDED_PRODUCT]->(p)
                        """)
                        interaction_count += 1
                    else:
                         # Don't create the AddToCartEvent if probability check fails
                         write_cypher(f, f"MATCH (interaction:CustomerInteraction {{interactionID: '{interaction_id}'}}) DELETE interaction") # Clean up base interaction


                elif action_type == "PageView":
                    page_url = fake.uri_path()
                    interaction_props["pageURL"] = page_url
                    interaction_props["timeOnPage"] = random.randint(5, 300) # seconds

                    # Set label
                    write_cypher(f, f"""
                        MATCH (interaction:CustomerInteraction {{interactionID: '{interaction_id}'}})
                        SET interaction:PageVisit
                        SET interaction.pageURL = '{page_url}', interaction.timeOnPage = {interaction_props["timeOnPage"]}
                     """)
                    interaction_count += 1

                else: # Other generic interaction
                    # Just keep the base CustomerInteraction node already created
                    interaction_count += 1

                if interaction_count < NUM_INTERACTIONS_SESSIONS : # Add to session list if created
                    session_interactions.append({"interactionID": interaction_id, "timestamp": current_time})


            session_end_time = current_time + datetime.timedelta(seconds=random.randint(30, 120))
            session_duration = (session_end_time - session_start_time).total_seconds()

            session_props = {
                "sessionID": session_id,
                "customerID": customer_id,
                "channelID": channel_id,
                "sessionStartTime": session_start_time,
                "sessionEndTime": session_end_time,
                "sessionDuration": int(session_duration),
                "device": random.choice(["Desktop", "Mobile", "Tablet"]),
                "browser": random.choice(["Chrome", "Firefox", "Safari", "Edge"]),
                "operatingSystem": random.choice(["Windows", "MacOS", "iOS", "Android"]),
                "hadRecommendationInteraction": session_has_recommendation, # H2 Flag
                "numberOfActions": len(session_interactions)
            }
            generated_data["web_sessions"].append(session_props)
            session_count += 1

            # Create WebSession node and link interactions
            write_cypher(f, f"CREATE (:CustomerWebSession {format_cypher_properties(session_props)})")
            for interaction_info in session_interactions:
                 write_cypher(f, f"""
                     MATCH (s:CustomerWebSession {{sessionID: '{session_id}'}}), (i:CustomerInteraction {{interactionID: '{interaction_info['interactionID']}'}})
                     CREATE (s)-[:CONTAINS_INTERACTION {{timestamp: i.interactionTimestamp}}]->(i)
                 """)

            # Simulate Cart Abandonment (H5)
            if session_products_added_to_cart and biased_boolean(CART_ABANDONMENT_RATE):
                # No purchase in this session path, record abandonment
                abandon_time = session_end_time + datetime.timedelta(seconds=1) # Occurs just after session theoretically ends
                abandon_event_id = generate_unique_id("evt-")
                abandon_props = {
                     "eventID": abandon_event_id,
                     "timestamp": abandon_time,
                     "customerID": customer_id,
                     "sessionID": session_id,
                     "reason": random.choice(["Price too high", "Checkout complex", "Distracted", "Technical issue", "Comparison shopping"]),
                 }
                # CREATE Event node and link
                write_cypher(f, f"CREATE (evt:CartAbandonmentEvent:SessionEvent {format_cypher_properties(abandon_props)})")
                write_cypher(f, f"MATCH (s:CustomerWebSession {{sessionID: '{session_id}'}}), (evt:CartAbandonmentEvent {{eventID: '{abandon_event_id}'}}) CREATE (s)-[:RESULTED_IN_ABANDONMENT]->(evt)")
                for item in session_products_added_to_cart:
                    write_cypher(f, f"MATCH (evt:CartAbandonmentEvent {{eventID: '{abandon_event_id}'}}), (p:Product {{productID: '{item['productID']}'}}) CREATE (evt)-[:ABANDONED_PRODUCT]->(p)")

                # Store info for potential recovery (H5)
                generated_data["abandoned_carts"].append({
                    "session_id": session_id,
                    "customer_id": customer_id,
                    "products": [p['productID'] for p in session_products_added_to_cart],
                    "abandon_time": abandon_time
                })
            elif session_products_added_to_cart:
                 session_ended_with_purchase = True # Flag for transaction generation

        else: # Non-session interaction (e.g., Call Center, simple Social interaction)
            interaction_id = generate_unique_id("int-")
            interaction_props = {
                "interactionID": interaction_id,
                "interactionTimestamp": interaction_time,
                "interactionType": "CustomerInquiry" if channel['channelCode'] == 'CallCenter' else "SocialMention",
                "channelID": channel_id,
                "customerID": customer_id
            }
            write_cypher(f, f"""
                MATCH (c:Customer {{customerID: '{customer_id}'}}), (ch:Channel {{channelID: '{channel_id}'}})
                CREATE (interaction:CustomerInteraction {format_cypher_properties(interaction_props)})
                CREATE (c)-[:HAD_INTERACTION {{timestamp: interaction.interactionTimestamp}}]->(interaction)
                CREATE (interaction)-[:OCCURRED_VIA_CHANNEL]->(ch)
            """)
            interaction_count += 1


def generate_sales_transactions_and_orders(f):
    """Generates SalesTransactions, Orders, and related nodes, biasing for Hypotheses."""
    print(f"Generating {NUM_SALES_TRANSACTIONS} transactions and {NUM_ORDERS} orders...")
    customers = generated_data["customers"]
    products = generated_data["products"]
    stores = generated_data["stores"]
    promotions = generated_data["promotions"]
    channels = generated_data["channels"]
    online_channels = [c for c in channels if c['channelCode'] in ['Web', 'MobileApp']]
    store_channel = next((c for c in channels if c['channelCode'] == 'Store'), None)
    # Create mapping for faster lookups if needed
    product_map = {p["productID"]: p for p in products}
    customer_map = {c["customerID"]: c for c in customers}
    promotion_map = {p["promotionID"]: p for p in promotions}

    # H5 Recovery Simulation
    recovered_carts_info = {} # Store { customer_id: { "recovered_at": datetime, "products": [...] } }
    abandoned_carts_to_process = list(generated_data["abandoned_carts"])
    random.shuffle(abandoned_carts_to_process)

    num_followups = int(len(abandoned_carts_to_process) * FOLLOWUP_AFTER_ABANDONMENT_PERCENTAGE)
    carts_with_followup = abandoned_carts_to_process[:num_followups]
    carts_without_followup = abandoned_carts_to_process[num_followups:]

    # Simulate recovery attempts for carts with follow-up
    for cart in carts_with_followup:
        if biased_boolean(H5_RECOVERY_RATE_WITH_FOLLOWUP):
            recovery_time = cart["abandon_time"] + datetime.timedelta(hours=random.uniform(1, 72))
            if recovery_time < datetime.datetime.now(datetime.timezone.utc): # Ensure recovery happens before now (if using tz) or end date
                recovered_carts_info[cart["customer_id"]] = {
                    "recovered_at": recovery_time,
                    "products": cart["products"]
                }
                # Optionally: Create a 'RecoveryInteraction' node here linked to the customer

    # Simulate recovery attempts for carts without follow-up
    for cart in carts_without_followup:
        if biased_boolean(H5_RECOVERY_RATE_NO_FOLLOWUP):
             recovery_time = cart["abandon_time"] + datetime.timedelta(hours=random.uniform(1, 168)) # Longer window perhaps
             if recovery_time < datetime.datetime.now(datetime.timezone.utc):
                 if cart["customer_id"] not in recovered_carts_info: # Avoid double recovery
                     recovered_carts_info[cart["customer_id"]] = {
                         "recovered_at": recovery_time,
                         "products": cart["products"]
                     }

    # Generate Orders and Transactions
    generated_orders = 0
    generated_transactions = 0

    # Prioritize recovered carts first for H5
    customer_ids_recovered = list(recovered_carts_info.keys())
    random.shuffle(customer_ids_recovered)

    for customer_id in customer_ids_recovered:
         if generated_orders >= NUM_ORDERS or generated_transactions >= NUM_SALES_TRANSACTIONS: break
         recovery_info = recovered_carts_info[customer_id]
         order_time = recovery_info["recovered_at"]
         # Ensure order time is after acquisition
         cust_acq_date = customer_map[customer_id]['acquisitionDate']
         if order_time.date() < cust_acq_date: continue # Skip if recovered before acquisition (unlikely but possible)

         channel = random.choice(online_channels) # Assume recovery happens online
         order_id = generate_unique_id("ord-")
         transaction_id = generate_unique_id("trx-")
         is_conversion = True # Recovered cart implies conversion
         order_lines = []
         total_amount = 0.0
         basket_size = 0

         # Use products from the recovered cart
         for prod_id in recovery_info["products"]:
              if prod_id in product_map:
                   product = product_map[prod_id]
                   qty = 1 # Assume quantity 1 for simplicity
                   line_price = product["productPrice"] * qty
                   order_lines.append({
                       "orderLineNumber": len(order_lines) + 1,
                       "productID": prod_id,
                       "quantity": qty,
                       "unitPrice": product["productPrice"],
                       "lineTotal": line_price
                   })
                   total_amount += line_price
                   basket_size += qty

         if not order_lines: continue # Skip if no valid products found

         # H4 AOV Bias (Check if this customer had social interaction)
         if customer_id in generated_data["social_journey_customers"]:
             total_amount *= H4_SOCIAL_AOV_MULTIPLIER
             # Could also increase basket_size here by adding another item

         # Create Order
         order_props = {
             "orderID": order_id,
             "customerID": customer_id,
             "orderDate": order_time.date(),
             "orderTimestamp": order_time,
             "orderStatus": "Processing", # Initial status
             "totalAmount": round(total_amount, 2),
             "currency": CURRENCY_CODE,
             "channelID": channel["channelID"],
             "isRecovery": True # H5 Flag
         }
         write_cypher(f, f"CREATE (:Order:SalesOrder {format_cypher_properties(order_props)})")
         generated_data["orders"].append(order_props)
         generated_orders += 1

         # Create Transaction
         transaction_props = {
             "transactionID": transaction_id,
             "orderID": order_id,
             "customerID": customer_id,
             "transactionTimestamp": order_time,
             "transactionType": "Sale",
             "totalAmount": round(total_amount, 2),
             "currency": CURRENCY_CODE,
             "channelID": channel["channelID"],
             "storeID": None, # Online order
             "isConversion": is_conversion,
             "isRecovery": True # H5 Flag
         }
         write_cypher(f, f"CREATE (:SalesTransaction:OnlineSaleTransaction {format_cypher_properties(transaction_props)})")
         generated_data["transactions"].append(transaction_props)
         generated_transactions += 1

         # Link Order, Transaction, Customer, Channel
         write_cypher(f, f"MATCH (o:Order {{orderID: '{order_id}'}}), (t:SalesTransaction {{transactionID: '{transaction_id}'}}) CREATE (o)-[:HAS_TRANSACTION]->(t)")
         write_cypher(f, f"MATCH (c:Customer {{customerID: '{customer_id}'}}), (o:Order {{orderID: '{order_id}'}}) CREATE (c)-[:PLACED_ORDER]->(o)")
         write_cypher(f, f"MATCH (ch:Channel {{channelID: '{channel['channelID']}'}}), (o:Order {{orderID: '{order_id}'}}) CREATE (o)-[:PLACED_VIA_CHANNEL]->(ch)")
         write_cypher(f, f"MATCH (ch:Channel {{channelID: '{channel['channelID']}'}}), (t:SalesTransaction {{transactionID: '{transaction_id}'}}) CREATE (t)-[:OCCURRED_ON_CHANNEL]->(ch)")
         write_cypher(f, f"MATCH (c:Customer {{customerID: '{customer_id}'}}), (t:SalesTransaction {{transactionID: '{transaction_id}'}}) CREATE (t)-[:PERFORMED_BY_CUSTOMER]->(c)")


         # Create Order Lines and Link
         for line in order_lines:
              line_id = generate_unique_id("ol-")
              line_props = line.copy() # Avoid modifying original dict
              line_props["orderLineID"] = line_id
              write_cypher(f, f"CREATE (:OrderLine:TransactionLine {format_cypher_properties(line_props)})")
              write_cypher(f, f"MATCH (o:Order {{orderID: '{order_id}'}}), (ol:OrderLine {{orderLineID: '{line_id}'}}) CREATE (o)-[:INCLUDES_ORDER_LINE]->(ol)")
              write_cypher(f, f"MATCH (t:SalesTransaction {{transactionID: '{transaction_id}'}}), (ol:OrderLine {{orderLineID: '{line_id}'}}) CREATE (t)-[:HAS_TRANSACTION_LINE]->(ol)")
              write_cypher(f, f"MATCH (p:Product {{productID: '{line['productID']}'}}), (ol:OrderLine {{orderLineID: '{line_id}'}}) CREATE (ol)-[:FOR_PRODUCT]->(p)")

         # Link back to recovery interaction/session if possible (more complex)
         # Find the original session?
         # MATCH (s:CustomerWebSession {sessionID: cart['session_id']}), (t:SalesTransaction {transactionID: transaction_id}) CREATE (t)-[:STEMMED_FROM_SESSION]->(s)

    # Generate remaining Orders/Transactions normally
    while generated_orders < NUM_ORDERS or generated_transactions < NUM_SALES_TRANSACTIONS:
        customer = random.choice(customers)
        customer_id = customer["customerID"]
        customer_info = customer_map[customer_id]
        is_loyalty_member = customer_info["isLoyaltyMember"]

        # Choose channel (more likely online)
        channel = random.choice(channels + online_channels*2) # Bias towards online
        channel_id = channel["channelID"]
        is_online = channel['channelCode'] in ['Web', 'MobileApp']
        store_id = None
        if not is_online and channel['channelCode'] == 'Store' and stores:
            store = random.choice(stores)
            store_id = store["storeID"]
            # Need to ensure Store channel node exists and channel_id matches the store's role as a channel if modeled that way
        elif not is_online: # If social/callcenter, maybe skip transaction or link differently? For now, assume Web/Mobile/Store.
             continue

        # Determine if transaction stems from a known session with specific triggers
        # This requires linking interaction outcomes, which is complex to simulate perfectly here.
        # We'll simulate the *outcomes* based on probability influenced by hypothesis flags.
        session_info = None # Find a preceding session for this customer? Too complex for now.
        had_recommendation = biased_boolean(SESSIONS_WITH_RECOMMENDATION_PERCENTAGE) # Simulate session characteristic
        viewed_high_viz_product = biased_boolean(HIGH_VIZ_PRODUCT_PERCENTAGE * 0.5) # Simulate seeing good product

        # H1: Promotion Application
        applied_promotion = None
        applicable_promotions = [p for p in promotions if p['promotionEndDate'] >= datetime.date.today()] # Simplistic filter
        targeted_promos = [p for p in applicable_promotions if p['isTargetedLoyalty']]
        general_promos = [p for p in applicable_promotions if not p['isTargetedLoyalty']]

        if is_loyalty_member and targeted_promos and random.random() < 0.4: # 40% chance loyal members use targeted promo
            applied_promotion = random.choice(targeted_promos)
        elif general_promos and random.random() < 0.2: # 20% chance anyone uses general promo
            applied_promotion = random.choice(general_promos)

        # Determine Conversion Probability based on Hypotheses
        base_conversion_prob = 0.1 # Base probability of a 'conversion' transaction event
        if is_online: base_conversion_prob = 0.3 # Online tends to have more tracking/sessions leading to conversion events

        # H1 Boost
        if applied_promotion and applied_promotion['isTargetedLoyalty'] and is_loyalty_member:
            base_conversion_prob *= H1_LOYALTY_CONVERSION_MULTIPLIER
        # H2 Boost
        if is_online and had_recommendation:
            base_conversion_prob *= H2_RECOMMENDATION_CONVERSION_MULTIPLIER
        # H3 Boost
        if is_online and viewed_high_viz_product:
             base_conversion_prob *= H3_VIZ_SCORE_CONVERSION_MULTIPLIER

        is_conversion = biased_boolean(min(base_conversion_prob, 0.98)) # Cap probability

        # Generate Transaction first
        if generated_transactions < NUM_SALES_TRANSACTIONS:
            transaction_id = generate_unique_id("trx-")
            order_time = get_random_datetime(start_date=customer_info['acquisitionDate'])
            # Build basket for transaction
            basket_size = random.randint(1, 8)
            order_lines = []
            total_amount = 0.0
            discount_amount = 0.0

            for i in range(basket_size):
                product = random.choice(products)
                if not product['isActive']: continue # Skip inactive
                prod_id = product['productID']
                qty = random.randint(1, 3)
                unit_price = product['productPrice']
                line_total = unit_price * qty
                order_lines.append({
                    "orderLineNumber": i + 1,
                    "productID": prod_id,
                    "quantity": qty,
                    "unitPrice": unit_price,
                    "lineTotal": line_total
                })
                total_amount += line_total

            if not order_lines: continue # Skip if basket is empty

            # Apply Promotion Discount
            if applied_promotion:
                promo_details = promotion_map[applied_promotion['promotionID']]
                if promo_details['promotionType'] == "Percentage Off":
                    discount_amount = total_amount * promo_details['discountPercentage']
                elif promo_details['promotionType'] == "Fixed Amount Off":
                    discount_amount = promo_details['discountAmount']
                # BOGO/Free Shipping logic not implemented here
                discount_amount = round(discount_amount, 2)
                total_amount -= discount_amount

            # H4 AOV Bias
            if customer_id in generated_data["social_journey_customers"]:
                total_amount *= H4_SOCIAL_AOV_MULTIPLIER # Apply multiplier before rounding

            total_amount = round(max(total_amount, 0.0), 2) # Ensure non-negative

            transaction_props = {
                "transactionID": transaction_id,
                "orderID": None, # Will be linked if order is created
                "customerID": customer_id,
                "transactionTimestamp": order_time,
                "transactionType": "Sale",
                "totalAmount": total_amount,
                "discountAmount": discount_amount,
                "currency": CURRENCY_CODE,
                "channelID": channel_id,
                "storeID": store_id, # Null if online
                "isConversion": is_conversion,
                "paymentMethod": random.choice(PAYMENT_METHODS),
                "appliedPromotionID": applied_promotion['promotionID'] if applied_promotion else None
            }
            tx_label = ":OnlineSaleTransaction" if is_online else ":InStoreSaleTransaction"
            write_cypher(f, f"CREATE (:SalesTransaction{tx_label} {format_cypher_properties(transaction_props)})")
            generated_data["transactions"].append(transaction_props)
            generated_transactions += 1

            # Link Transaction basics
            write_cypher(f, f"MATCH (c:Customer {{customerID: '{customer_id}'}}), (t:SalesTransaction {{transactionID: '{transaction_id}'}}) CREATE (t)-[:PERFORMED_BY_CUSTOMER]->(c)")
            write_cypher(f, f"MATCH (ch:Channel {{channelID: '{channel_id}'}}), (t:SalesTransaction {{transactionID: '{transaction_id}'}}) CREATE (t)-[:OCCURRED_ON_CHANNEL]->(ch)")
            if store_id:
                write_cypher(f, f"MATCH (s:Store {{storeID: '{store_id}'}}), (t:SalesTransaction {{transactionID: '{transaction_id}'}}) CREATE (t)-[:OCCURRED_AT_STORE]->(s)")
            if applied_promotion:
                 write_cypher(f, f"MATCH (p:Promotion {{promotionID: '{applied_promotion['promotionID']}'}}), (t:SalesTransaction {{transactionID: '{transaction_id}'}}) CREATE (t)-[:APPLIED_PROMOTION]->(p)")

            # Create Order only if it's a conversion and we haven't hit the order limit
            if is_conversion and generated_orders < NUM_ORDERS:
                order_id = generate_unique_id("ord-")
                order_props = {
                    "orderID": order_id,
                    "customerID": customer_id,
                    "orderDate": order_time.date(),
                    "orderTimestamp": order_time,
                    "orderStatus": random.choice(["Processing", "Shipped"]),
                    "totalAmount": total_amount,
                    "currency": CURRENCY_CODE,
                    "channelID": channel_id,
                    "deliveryMethod": random.choice(DELIVERY_METHODS) if is_online else "In-Store Pickup",
                    "shippingAddress": fake.address() if is_online else None,
                    "isRecovery": False
                }
                write_cypher(f, f"CREATE (:Order:SalesOrder {format_cypher_properties(order_props)})")
                generated_data["orders"].append(order_props)
                generated_orders += 1

                # Link Order and Transaction
                write_cypher(f, f"MATCH (o:Order {{orderID: '{order_id}'}}), (t:SalesTransaction {{transactionID: '{transaction_id}'}}) CREATE (o)-[:HAS_TRANSACTION]->(t)")
                write_cypher(f, f"MATCH (c:Customer {{customerID: '{customer_id}'}}), (o:Order {{orderID: '{order_id}'}}) CREATE (c)-[:PLACED_ORDER]->(o)")
                write_cypher(f, f"MATCH (ch:Channel {{channelID: '{channel_id}'}}), (o:Order {{orderID: '{order_id}'}}) CREATE (o)-[:PLACED_VIA_CHANNEL]->(ch)")
                # Update transaction with order ID
                write_cypher(f, f"MATCH (t:SalesTransaction {{transactionID: '{transaction_id}'}}) SET t.orderID = '{order_id}'")


                # Create Order Lines and link to Order, Transaction, Product
                for line in order_lines:
                    line_id = generate_unique_id("ol-")
                    line_props = line.copy()
                    line_props["orderLineID"] = line_id
                    # Apply promotion discount proportionally? Simplified: discount applied at transaction level.
                    write_cypher(f, f"CREATE (:OrderLine:TransactionLine {format_cypher_properties(line_props)})")
                    write_cypher(f, f"MATCH (o:Order {{orderID: '{order_id}'}}), (ol:OrderLine {{orderLineID: '{line_id}'}}) CREATE (o)-[:INCLUDES_ORDER_LINE]->(ol)")
                    write_cypher(f, f"MATCH (t:SalesTransaction {{transactionID: '{transaction_id}'}}), (ol:OrderLine {{orderLineID: '{line_id}'}}) CREATE (t)-[:HAS_TRANSACTION_LINE]->(ol)")
                    write_cypher(f, f"MATCH (p:Product {{productID: '{line['productID']}'}}), (ol:OrderLine {{orderLineID: '{line_id}'}}) CREATE (ol)-[:FOR_PRODUCT]->(p)")

            # If it wasn't a conversion, but had lines, still link lines to transaction (as basket items?)
            elif not is_conversion and order_lines:
                 for line in order_lines:
                    # Create TransactionLine only, link to transaction
                    line_id = generate_unique_id("tl-")
                    line_props = line.copy()
                    line_props["transactionLineID"] = line_id # Use different ID or naming?
                    write_cypher(f, f"CREATE (:TransactionLine {format_cypher_properties(line_props)})") # No OrderLine label
                    write_cypher(f, f"MATCH (t:SalesTransaction {{transactionID: '{transaction_id}'}}), (tl:TransactionLine {{transactionLineID: '{line_id}'}}) CREATE (t)-[:HAS_TRANSACTION_LINE]->(tl)")
                    write_cypher(f, f"MATCH (p:Product {{productID: '{line['productID']}'}}), (tl:TransactionLine {{transactionLineID: '{line_id}'}}) CREATE (tl)-[:FOR_PRODUCT]->(p)")


def add_constraints(f):
    """Adds unique constraints for faster lookups during relationship creation."""
    print("Adding constraints...")
    write_cypher(f, "CREATE CONSTRAINT unique_customer_id IF NOT EXISTS FOR (c:Customer) REQUIRE c.customerID IS UNIQUE;")
    write_cypher(f, "CREATE CONSTRAINT unique_product_id IF NOT EXISTS FOR (p:Product) REQUIRE p.productID IS UNIQUE;")
    write_cypher(f, "CREATE CONSTRAINT unique_product_sku IF NOT EXISTS FOR (p:Product) REQUIRE p.productSKU IS UNIQUE;")
    write_cypher(f, "CREATE CONSTRAINT unique_store_id IF NOT EXISTS FOR (s:Store) REQUIRE s.storeID IS UNIQUE;")
    write_cypher(f, "CREATE CONSTRAINT unique_order_id IF NOT EXISTS FOR (o:Order) REQUIRE o.orderID IS UNIQUE;")
    write_cypher(f, "CREATE CONSTRAINT unique_transaction_id IF NOT EXISTS FOR (t:SalesTransaction) REQUIRE t.transactionID IS UNIQUE;")
    write_cypher(f, "CREATE CONSTRAINT unique_promotion_id IF NOT EXISTS FOR (p:Promotion) REQUIRE p.promotionID IS UNIQUE;")
    write_cypher(f, "CREATE CONSTRAINT unique_campaign_id IF NOT EXISTS FOR (c:Campaign) REQUIRE c.campaignID IS UNIQUE;")
    write_cypher(f, "CREATE CONSTRAINT unique_channel_id IF NOT EXISTS FOR (c:Channel) REQUIRE c.channelID IS UNIQUE;")
    write_cypher(f, "CREATE CONSTRAINT unique_session_id IF NOT EXISTS FOR (s:CustomerWebSession) REQUIRE s.sessionID IS UNIQUE;")
    write_cypher(f, "CREATE CONSTRAINT unique_interaction_id IF NOT EXISTS FOR (i:CustomerInteraction) REQUIRE i.interactionID IS UNIQUE;")
    write_cypher(f, "CREATE CONSTRAINT unique_supplier_id IF NOT EXISTS FOR (s:Supplier) REQUIRE s.supplierID IS UNIQUE;")
    write_cypher(f, "CREATE CONSTRAINT unique_brand_id IF NOT EXISTS FOR (b:Brand) REQUIRE b.brandID IS UNIQUE;")
    write_cypher(f, "CREATE CONSTRAINT unique_loyalty_program_id IF NOT EXISTS FOR (lp:LoyaltyProgram) REQUIRE lp.loyaltyProgramID IS UNIQUE;")
    write_cypher(f, "CREATE CONSTRAINT unique_loyalty_tier_id IF NOT EXISTS FOR (lt:LoyaltyTier) REQUIRE lt.loyaltyTierID IS UNIQUE;")
    write_cypher(f, "CREATE CONSTRAINT unique_segment_id IF NOT EXISTS FOR (cs:CustomerSegment) REQUIRE cs.segmentID IS UNIQUE;")
    write_cypher(f, "\n") # Add a newline for readability


# --- Main Execution ---
if __name__ == "__main__":
    start_time = datetime.datetime.now()
    print(f"Starting data generation at {start_time}...")

    with open(OUTPUT_CYPHER_FILE, "w", encoding="utf-8") as f:
        # Start transaction (optional, but good for large imports)
        # write_cypher(f, "BEGIN") # Use with caution or specific import tools

        # 1. Add Constraints (important for performance)
        add_constraints(f)

        # 2. Generate Foundational Nodes
        generate_foundational_nodes(f)

        # 3. Generate Core Entity Nodes
        generate_stores(f)
        generate_suppliers(f)
        generate_products(f) # Generates brands implicitly if needed, links later
        generate_customers(f) # Handles loyalty segment linking

        # 4. Generate Marketing Nodes
        generate_promotions_campaigns(f) # Depends on loyalty segment

        # 5. Generate Interaction & Transactional Nodes (Hypothesis Biasing Happens Here)
        generate_interactions_and_sessions(f) # Depends on customers, products, channels. Creates abandoned carts list.
        generate_sales_transactions_and_orders(f) # Depends on interactions/abandonment, customers, products, stores, promos.

        # 6. Generate other nodes if needed (Events, Inventory, POs, etc.)
        # (Skipped for brevity, but follow similar patterns)
        print("Skipping generation of Events, Inventory, POs for this example.")

        # Commit transaction (if BEGIN was used)
        # write_cypher(f, "COMMIT")

    end_time = datetime.datetime.now()
    print(f"\nFinished data generation at {end_time}.")
    print(f"Total time: {end_time - start_time}")
    print(f"Cypher queries written to: {OUTPUT_CYPHER_FILE}")
    print("\n--- Summary ---")
    print(f"Customers: {len(generated_data['customers'])}")
    print(f"Products: {len(generated_data['products'])}")
    print(f"Stores: {len(generated_data['stores'])}")
    print(f"Suppliers: {len(generated_data['suppliers'])}")
    print(f"Promotions: {len(generated_data['promotions'])}")
    print(f"Web Sessions: {len(generated_data['web_sessions'])}")
    print(f"Orders: {len(generated_data['orders'])}")
    print(f"Transactions: {len(generated_data['transactions'])}")
    print(f"Abandoned Carts Tracked: {len(generated_data['abandoned_carts'])}")
    print(f"Customers with Social Interaction: {len(generated_data['social_journey_customers'])}")