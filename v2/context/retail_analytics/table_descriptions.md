# Schema Table Descriptions: retail_analytics

This document contains table-level long descriptions and compact column meanings.

## addresses

This table stores the physical and mailing addresses associated with clients. Each record represents a unique address and includes full street details, city, county, state, ZIP code, country, timezone offset, and the type of dwelling (apartment, condo, or single family). It is used to support geographic segmentation and regional analysis of customers across purchases, refunds, and marketing activity. All addresses in this dataset are located in the United States. The table links to client records, billing addresses, and shipping addresses across the store, online, and mail-order purchase channels.

| Column | Short description |
|---|---|
| `address_code` | Business-facing code that uniquely identifies an address. |
| `address_record` | Internal unique identifier for each address record. |
| `city` | City for the client address. |
| `country` | Country for the client address. All records are United States. |
| `county` | The county associated with the client's address. |
| `location_type` | The type of dwelling at the address, such as apartment, condo, or single family home. |
| `state` | US state abbreviation for the client address. |
| `street_name` | The name of the street for the client's address. |
| `street_number` | The numeric portion of the street address, such as a house or building number. |
| `street_type` | The type or suffix of the street, such as Avenue, Boulevard, or Road. |
| `suite_number` | The suite or unit designation within a building, if applicable. |
| `timezone_offset` | UTC timezone offset for the address location, ranging from -10 to -5 hours. |
| `zip` | Postal ZIP code for the client address. |

## calendar_days

This table provides a comprehensive date dimension covering every calendar day from 1900 through 2100. Each row represents a single date and includes attributes for standard calendar reporting (year, quarter, month, week, day of week, day of month) as well as fiscal calendar equivalents (fiscal year, fiscal quarter sequence, fiscal week sequence). It also includes flags and indicators for holidays, weekends, days following a holiday, first and last days of the month, and whether a date falls within the current day, week, month, quarter, or year. Cross-period comparison fields such as same day last year and same day last quarter support year-over-year and quarter-over-quarter trend analysis. This table is referenced by purchase, refund, stock level, and campaign tables to enable time-based reporting.

| Column | Short description |
|---|---|
| `calendar_day` | The actual calendar date for this record. |
| `calendar_day_code` | Business-facing code that uniquely identifies a calendar day. |
| `calendar_day_record` | Internal unique identifier for each calendar day record. |
| `current_day` | Flags whether this date is today (Y/N). All values are currently N in this dataset. |
| `current_month` | Flags whether this date falls in the current calendar month (Y/N). |
| `current_quarter` | Flags whether this date falls in the current calendar quarter (Y/N). |
| `current_week` | Flags whether this date falls in the current week (Y/N). All values are currently N in this dataset. |
| `current_year` | Flags whether this date falls in the current calendar year (Y/N). |
| `day_name` | The full name of the day of the week, such as Monday or Friday. |
| `day_of_month` | The day number within the month, from 1 to 31. |
| `day_of_week` | Numeric day of the week, where 0 through 6 represent the days Sunday through Saturday (or similar convention). |
| `first_day_of_month` | Reference to the first day of the month containing this date. |
| `fiscal_quarter_sequence` | A sequential number identifying the fiscal quarter across all fiscal years. |
| `fiscal_week_sequence` | A sequential number identifying the fiscal week across all fiscal years. |
| `fiscal_year` | The fiscal year associated with the calendar date. |
| `following_holiday` | Indicates whether the date immediately follows a public holiday (Y/N). |
| `holiday` | Indicates whether the date is a public holiday (Y/N). |
| `last_day_of_month` | Reference to the last day of the month containing this date. |
| `month_of_year` | Month number within the year, from 1 (January) to 12 (December). |
| `month_sequence` | A sequential number identifying the month across all years in the calendar. |
| `quarter_name` | A human-readable label for the calendar quarter, such as '2024Q3'. |
| `quarter_of_year` | The quarter number within the calendar year, from 1 (Q1) to 4 (Q4). |
| `quarter_sequence` | A sequential number identifying the quarter across all years in the calendar. |
| `same_day_last_quarter` | Reference to the equivalent date one quarter prior, for quarter-over-quarter comparison. |
| `same_day_last_year` | Reference to the equivalent date one year prior, for year-over-year comparison. |
| `week_sequence` | A sequential number identifying the week across all years in the calendar. |
| `weekend` | Indicates whether the date falls on a weekend (Y/N). |
| `year` | Calendar year for the date record. |

## client_profiles

This table contains demographic profile records used to classify individual shoppers into segments for analysis. Each record captures a combination of demographic attributes: gender (male or female), marital status (single, married, divorced, widowed, or unknown), education level (ranging from primary through advanced degree), an estimated annual purchase amount, credit rating (Good, Low Risk, High Risk, or Unknown), and counts of total dependents, employed dependents, and dependents in college. With nearly 1.9 million rows, this table represents a large set of demographic combinations. Client records link to their current active profile via a reference key. These segments are used to analyze purchasing behavior, revenue, and campaign response across different customer groups.

| Column | Short description |
|---|---|
| `client_profile_record` | Internal unique identifier for each client demographic profile record. |
| `credit_rating` | Client credit rating segment: Good, Low Risk, High Risk, or Unknown. |
| `dependent_college_count` | Number of dependents in the client's household currently attending college, from 0 to 6. |
| `dependent_count` | Total number of dependents in the client's household, from 0 to 6. |
| `dependent_employed_count` | Number of employed dependents in the client's household, from 0 to 6. |
| `education_status` | Client education segment, ranging from Primary through Advanced Degree. |
| `gender` | Client gender segment: Male (M) or Female (F). |
| `marital_status` | Client marital status segment: Single, Married, Divorced, Widowed, or Unknown. |
| `purchase_estimate` | Estimated annual purchase amount for the client, in dollars. |

## clients

The clients table is the central record for every shopper or customer account in the business. Each row represents one person and links to their current mailing address, demographic profile, and household profile. The table also captures key tenure milestones such as the date of the customer's first purchase and first shipment, along with personal details like name, salutation, date of birth, birth country, email address, and a preferred-customer flag. It serves as the starting point for customer segmentation, loyalty analysis, lifetime value reporting, and cross-channel purchase history.

| Column | Short description |
|---|---|
| `birth_country` | Country where the customer was born, used for demographic and geographic analysis. |
| `birth_day` | Day of the month the customer was born, used for birthday-based analysis. |
| `birth_month` | Month the customer was born, used for age and birthday analysis. |
| `birth_year` | Year the customer was born, used to calculate age and generational segments. |
| `client_code` | Business-facing customer identifier used to reference a shopper across systems. |
| `client_record` | Internal system identifier uniquely identifying each client record. |
| `current_address_ref` | Links the client to their current mailing or residential address. |
| `current_client_profile_ref` | Links the client to their active demographic profile record. |
| `current_household_profile_ref` | Links the client to their active household profile record. |
| `email_address` | Customer's email address used for digital communications and account identification. |
| `first_name` | Customer's first or given name. |
| `first_sale_calendar_day_ref` | Calendar day of the customer's first purchase, indicating when they first bought from the business. |
| `first_shipping_calendar_day_ref` | Calendar day of the customer's first shipment, indicating when they first received an order. |
| `last_name` | Customer's last name or surname. |
| `last_review_calendar_day_ref` | Calendar day of the most recent review or account assessment for the customer. |
| `login` | Customer's login or username for online account access. |
| `preferred_client_flag` | Indicates whether the customer is a preferred or loyalty-tier shopper. |
| `salutation` | Customer's preferred title or salutation, such as Mr., Mrs., Dr., or Ms. |

## clock_times

The clock_times table is a time-of-day dimension containing one row for every second in a 24-hour day (86,400 rows total). It supports analysis of when purchases, returns, or other events occur throughout the day. Each record provides the exact hour, minute, and second, along with AM/PM indicator, work shift (first, second, third), sub-shift period (morning, afternoon, evening, night), and meal period (breakfast, lunch, dinner). This table is used to answer questions such as which shift drives the most sales or whether purchases spike during lunch hours.

| Column | Short description |
|---|---|
| `am_pm` | Indicates whether the time falls in the AM or PM half of the day. |
| `clock_time` | Numeric representation of the time of day in seconds since midnight. |
| `clock_time_code` | Business-facing alphanumeric code identifying each second-level time record. |
| `clock_time_record` | Internal system identifier uniquely identifying each second-level time record. |
| `hour` | Hour of the day (0–23) for time-of-day analysis. |
| `meal_clock_time` | Meal period associated with the time of day, such as breakfast, lunch, or dinner. |
| `minute` | Minute within the hour (0–59) for time-of-day analysis. |
| `second` | Second within the minute (0–59) for precise time-of-day analysis. |
| `shift` | Work shift period (first, second, or third) associated with the time of day. |
| `sub_shift` | Finer time-of-day period such as morning, afternoon, evening, or night. |

## delivery_methods

The delivery_methods table is a small reference table describing the 20 shipping and delivery options available to customers. Each record identifies the service type (such as EXPRESS, NEXT DAY, OVERNIGHT, REGULAR, or TWO DAY), the transport mode or code (AIR, BIKE, SEA, SURFACE), the carrier name (such as FedEx, UPS, DHL, or USPS), and the associated carrier contract identifier. This table is used to analyze order fulfillment by shipping speed, carrier performance, and delivery channel across store, online, and mail-order purchases.

| Column | Short description |
|---|---|
| `carrier` | Name of the shipping carrier or logistics provider for the delivery method. |
| `code` | Transport mode code for the delivery method, such as Air, Sea, or Surface. |
| `contract` | Contract identifier associated with the carrier agreement for this delivery method. |
| `delivery_method_code` | Business-facing alphanumeric code identifying each delivery method. |
| `delivery_method_record` | Internal system identifier uniquely identifying each delivery method record. |
| `type` | Delivery service type or speed, such as Express, Next Day, Overnight, or Regular. |

## fulfillment_centers

The fulfillment_centers table describes the five warehouse or distribution center locations used by the business to store inventory and ship orders. Each record includes the warehouse name, physical size in square feet, and a full mailing address (street, city, county, state, ZIP, country, and timezone offset). This table is referenced by purchase and stock-level records to identify which warehouse fulfilled an order or holds a given product. All five centers in the current data appear to be located in Fairview, TN, United States. Some address and size fields have a small number of null values.

| Column | Short description |
|---|---|
| `city` | City where the fulfillment center is located. |
| `country` | Country where the fulfillment center is located. |
| `county` | County where the fulfillment center is located. |
| `fulfillment_center_code` | Business-facing alphanumeric code identifying each fulfillment center or warehouse. |
| `fulfillment_center_record` | Internal system identifier uniquely identifying each fulfillment center record. |
| `state` | US state where the fulfillment center is located. |
| `street_name` | Street name of the fulfillment center's physical address. |
| `street_number` | Street number of the fulfillment center's physical address. |
| `street_type` | Street type suffix for the fulfillment center's address, such as Avenue, Drive, or Parkway. |
| `suite_number` | Suite or unit number within the fulfillment center's building address. |
| `timezone_offset` | UTC timezone offset for the fulfillment center's location. |
| `warehouse_name` | Name of the fulfillment center or warehouse facility. |
| `warehouse_square_ft` | Physical size of the warehouse in square feet. |
| `zip` | Postal ZIP code of the fulfillment center's location. |

## household_profiles

The household_profiles table contains segment records that classify households along key economic and lifestyle dimensions. Each record describes a unique combination of income range, estimated buying potential, number of dependents, and vehicle count. These segments are linked to clients through the clients table and are used to analyze purchasing behavior, target marketing campaigns, and understand the economic profile of shoppers. Buying potential is expressed as a dollar range (e.g., '1001-5000', '>10000'), making it useful for segmenting high-value versus low-value households. The table contains 7,200 distinct segment combinations and serves as a reference dimension for retail analytics across all purchase and refund channels.

| Column | Short description |
|---|---|
| `buy_potential` | Estimated annual spending potential of the household, expressed as a dollar range such as '0-500', '1001-5000', or '>10000'. |
| `dependent_count` | Number of dependents in the household, ranging from 0 to 9. |
| `household_profile_record` | Internal unique identifier for each household profile segment record. |
| `income_range_ref` | Links the household profile to an income band in the income_ranges table, indicating the household's estimated annual income tier. |
| `vehicle_count` | Number of vehicles owned by the household, with -1 indicating unknown or not applicable. |

## income_ranges

The income_ranges table is a small reference dimension containing 20 income band records. Each record defines a contiguous income bracket with a lower bound and an upper bound expressed in dollars (e.g., $0–$10,000 up to $190,001–$200,000). These bands are used to classify households into economic tiers via the household_profiles table. Analysts and business users can use this table to filter or group customers by income level, compare purchasing behavior across income segments, or evaluate campaign effectiveness by economic tier. The bands are evenly spaced at $10,000 intervals, covering a range from $0 to $200,000.

| Column | Short description |
|---|---|
| `income_range_record` | Internal unique identifier for each income band record in the income_ranges reference table. |
| `lower_bound` | The minimum annual household income (in dollars) for this income band, e.g., $0, $10,001, $20,001. |
| `upper_bound` | The maximum annual household income (in dollars) for this income band, e.g., $10,000, $20,000, up to $200,000. |

## mail_order_purchases

The mail_order_purchases table records every item sold through the mail-order (catalog or mailer) channel. With over 1.4 million rows, it is the primary fact table for mail-order commerce analysis. Each row represents a line item within an order and captures the full financial picture: wholesale cost, list price, actual sales price, extended amounts for quantity, discounts, delivery costs, tax, and multiple net-paid totals. Profitability is available via net_profit. The table links to calendar days for sale date and shipping date, clock times for time-of-day analysis, clients (both billing and shipping), demographic profiles (client and household), addresses (billing and shipping), the support center that handled the order, the mailer page that prompted the purchase, the delivery method used, the fulfillment center that shipped the order, the merchandise item sold, and the marketing campaign associated with the sale. This makes it suitable for analyzing mail-order revenue, order volume, discount effectiveness, coupon usage, delivery costs, campaign ROI, customer segments, and fulfillment performance.

| Column | Short description |
|---|---|
| `billing_address_ref` | The billing address associated with the mail-order purchase, linking to the addresses table. |
| `billing_client_profile_ref` | The demographic profile of the billing customer at the time of the mail-order purchase, linking to the client_profiles table. |
| `billing_client_ref` | The customer account responsible for payment on the mail-order purchase, linking to the clients table. |
| `billing_household_profile_ref` | The household segment of the billing customer at the time of the mail-order purchase, linking to the household_profiles table. |
| `campaign_ref` | The marketing campaign or promotional offer associated with the mail-order purchase, linking to the marketing_campaigns table. |
| `coupon_amount` | The total coupon or voucher savings applied to this mail-order line item. |
| `delivery_method_ref` | The shipping or delivery method used for the mail-order order, linking to the delivery_methods table. |
| `extended_delivery_cost` | The total shipping or delivery cost charged for this mail-order line item. |
| `extended_discount_amount` | The total discount applied across all units in this mail-order line item (quantity × per-unit discount). |
| `extended_list_price` | The total value of this mail-order line item at the full list price before discounts (quantity × list price). |
| `extended_sales_price` | The total revenue from this mail-order line item at the actual sales price (quantity × sales price). |
| `extended_tax` | The total tax amount applied to this mail-order line item. |
| `extended_wholesale_cost` | The total wholesale cost for this mail-order line item (quantity × wholesale cost per unit). |
| `fulfillment_center_ref` | The warehouse or fulfillment center that shipped the mail-order order, linking to the fulfillment_centers table. |
| `list_price` | The standard retail or catalog list price per unit for the product in this mail-order line item. |
| `mailer_page_ref` | The catalog or mailer page that prompted the mail-order purchase, linking to the mailer_pages table. |
| `merchandise_ref` | The product sold in the mail-order transaction, linking to the merchandise catalog table. |
| `net_paid` | The net amount paid by the customer for this mail-order line item, excluding tax and delivery charges. |
| `net_paid_with_delivery` | The net amount paid by the customer for this mail-order line item, including delivery charges but excluding tax. |
| `net_paid_with_delivery_tax` | The fully loaded amount paid by the customer for this mail-order line item, including both delivery charges and tax. |
| `net_paid_with_tax` | The net amount paid by the customer for this mail-order line item, including tax but excluding delivery charges. |
| `net_profit` | The profit or loss on this mail-order line item, calculated as net revenue minus wholesale cost. |
| `order_number` | The mail-order order identifier, grouping multiple line items that belong to the same customer order. |
| `quantity` | The number of units of the product ordered in this mail-order line item. |
| `sale_calendar_day_ref` | The calendar date when the mail-order purchase was placed, linking to the calendar_days reference table. |
| `sale_clock_time_ref` | The time of day when the mail-order purchase was placed, linking to the clock_times reference table. |
| `sales_price` | The actual per-unit price charged to the customer for the product in this mail-order line item, after discounts. |
| `shipping_address_ref` | The delivery address for the mail-order shipment, linking to the addresses table. |
| `shipping_calendar_day_ref` | The calendar date when the mail-order purchase was shipped, linking to the calendar_days reference table. |
| `shipping_client_profile_ref` | The demographic profile of the shipping recipient at the time of the mail-order purchase, linking to the client_profiles table. |
| `shipping_client_ref` | The customer account receiving the mail-order shipment, linking to the clients table. |
| `shipping_household_profile_ref` | The household segment of the shipping recipient at the time of the mail-order purchase, linking to the household_profiles table. |
| `support_center_ref` | The customer support or call center that handled the mail-order purchase, linking to the support_centers table. |
| `wholesale_cost` | The per-unit cost paid by the business to acquire the product for this mail-order line item. |

## mail_order_refunds

This table records every item return or refund processed through the mail-order (catalog) channel. Each row represents a single return event and includes the date and time of the return, the product returned, the order it came from, and the quantity sent back. Two client roles are tracked: the client who was originally billed or refunded (refunded client) and the client who physically sent the item back (returning client). Both roles are linked to their demographic profiles and household profiles at the time of the return, enabling segmentation analysis. Financial columns cover the gross return amount, applicable taxes, processing fees, shipping costs for the return, and the breakdown of how the refund was issued—cash refund, charge reversal, or store credit. The net loss column summarizes the total financial impact of the return to the business. Support center, mailer page, delivery method, and fulfillment center references allow analysis of which operational units handled the return. Return reason links to a reference table describing why the item was sent back.

| Column | Short description |
|---|---|
| `delivery_method_ref` | The shipping or delivery method used for the return. |
| `fee` | A processing or restocking fee charged to the customer for the mail-order return. |
| `fulfillment_center_ref` | The fulfillment center or warehouse that received the returned item. |
| `mailer_page_ref` | The catalog or mailer page associated with the returned item. |
| `merchandise_ref` | The product item that was returned in this mail-order refund. |
| `net_loss` | The total financial loss to the business from this mail-order return. |
| `order_number` | The mail-order order number associated with this return. |
| `refunded_address_ref` | The mailing address of the customer who received the refund. |
| `refunded_cash` | The portion of the refund paid back to the customer as cash. |
| `refunded_client_profile_ref` | The demographic profile of the customer who received the refund. |
| `refunded_client_ref` | The customer who received the refund payment for the returned mail-order item. |
| `refunded_household_profile_ref` | The household profile of the customer who received the refund. |
| `return_amount` | The gross refund amount before tax for the returned mail-order items. |
| `return_amount_with_tax` | The total refund amount including tax for the returned mail-order items. |
| `return_calendar_day_ref` | The calendar date when the mail-order return was processed. |
| `return_clock_time_ref` | The time of day when the mail-order return was processed. |
| `return_delivery_cost` | The shipping cost incurred to return the mail-order item. |
| `return_quantity` | The number of units returned in this mail-order refund transaction. |
| `return_reason_ref` | The reason the customer returned the mail-order item. |
| `return_tax` | The tax portion of the mail-order refund amount. |
| `returning_address_ref` | The address of the customer who physically returned the item. |
| `returning_client_profile_ref` | The demographic profile of the customer who physically returned the item. |
| `returning_client_ref` | The customer who physically sent the item back. |
| `returning_household_profile_ref` | The household profile of the customer who physically returned the item. |
| `reversed_charge` | The portion of the refund issued as a charge reversal or credit card chargeback. |
| `store_credit` | The portion of the refund issued as store credit or account credit. |
| `support_center_ref` | The customer support center that handled this mail-order return. |

## mailer_pages

This table describes individual pages within printed catalogs or mailers distributed to customers. Each record represents one page within a specific mailer issue and includes the mailer number (which catalog edition) and the page number within that mailer. Date references indicate when the mailer was active—its start and end calendar days—allowing analysis of which catalog editions drove purchases or returns during a given period. The department field categorizes the page by merchandise department, though the current data shows a placeholder value, suggesting this field may not yet be fully populated. The description field provides a free-text summary of the page content. The type field classifies the mailer cadence as monthly, quarterly, or bi-annual, which is useful for understanding promotion frequency and seasonality. This table is used as a dimension in mail-order purchase and refund analysis to connect transactions back to the specific catalog page that influenced the sale or return.

| Column | Short description |
|---|---|
| `department` | The merchandise department featured on this mailer page. |
| `description` | A free-text description of the content featured on this mailer page. |
| `end_calendar_day_ref` | The last date the mailer or catalog containing this page was active. |
| `mailer_number` | The catalog or mailer edition number this page belongs to. |
| `mailer_page_code` | Business-facing code that uniquely identifies a mailer page. |
| `mailer_page_number` | The page number within the catalog or mailer. |
| `mailer_page_record` | Internal row identifier for each mailer page record. |
| `start_calendar_day_ref` | The first date the mailer or catalog containing this page was active. |
| `type` | The publication frequency or cadence of the mailer containing this page. |

## marketing_campaigns

The marketing_campaigns table contains one record per marketing campaign or promotional offer run by the business. Each campaign has a name, a start and end date, a budget cost, and flags indicating which channels were used to reach customers — such as direct mail, email, mailer, TV, radio, press, event, or profile-based targeting. Campaigns can be linked to a specific merchandise item being promoted. The table supports analysis of campaign reach, channel mix, promotional timing, and the relationship between marketing activity and sales or revenue outcomes. The purpose field is currently recorded as 'Unknown' for most campaigns, and the discount_active flag indicates whether a discount was active during the campaign. With 300 campaigns in the dataset, this table is the primary reference for marketing attribution across store, online, and mail-order purchase channels.

| Column | Short description |
|---|---|
| `campaign_code` | Business-facing alphanumeric code that uniquely identifies each marketing campaign. |
| `campaign_name` | The name or label assigned to the marketing campaign. |
| `campaign_record` | Internal row identifier for each marketing campaign record. |
| `channel_details` | Free-text description providing additional details about the campaign's channel or messaging approach. |
| `channel_direct_mail` | Indicates whether this campaign used the direct mail channel (Y/N). |
| `channel_email` | Indicates whether this campaign used the email channel (Y/N). |
| `channel_event` | Indicates whether this campaign used an event-based channel (Y/N). |
| `channel_mailer` | Indicates whether this campaign used the catalog mailer channel (Y/N). |
| `channel_press` | Indicates whether this campaign used the press or print media channel (Y/N). |
| `channel_profile` | Indicates whether this campaign used profile-based or targeted demographic channel (Y/N). |
| `channel_radio` | Indicates whether this campaign used the radio channel (Y/N). |
| `channel_tv` | Indicates whether this campaign used the television channel (Y/N). |
| `cost` | The total budget or spend associated with running this marketing campaign. |
| `discount_active` | Indicates whether a discount was active during this campaign (Y/N). |
| `end_calendar_day_ref` | The calendar day when the campaign ended, linked to the calendar_days reference table. |
| `merchandise_ref` | The product promoted by this campaign, linked to the merchandise table. |
| `purpose` | The stated purpose or objective of the marketing campaign. |
| `response_target` | The target number of customer responses expected from this campaign. |
| `start_calendar_day_ref` | The calendar day when the campaign began, linked to the calendar_days reference table. |

## merchandise

The merchandise table is the central product catalog for the business, containing up to 18,000 product records. Each record describes a sellable item with attributes including product name, description, brand, product class, category, manufacturer, size, color, formulation, units of measure, and container type. Pricing information includes the current retail price and wholesale cost. The table supports slowly changing history through record_effective_start and record_effective_end dates, allowing analysis of how product attributes or pricing changed over time. Categories span Books, Children, Electronics, Home, Jewelry, Men, Music, Shoes, Sports, and Women. Classes include detailed groupings such as kids, shirts, fragrances, swimwear, dresses, and many others. This table is joined to purchase, refund, stock level, and campaign tables to analyze sales performance, profitability, inventory, and promotional effectiveness by product, brand, category, or manufacturer.

| Column | Short description |
|---|---|
| `brand` | The brand name of the product. |
| `brand_code` | Numeric code identifying the product's brand, used for joining or grouping by brand. |
| `category` | The top-level product category, such as Books, Electronics, Shoes, or Women. |
| `category_code` | Numeric code identifying the product's top-level category. |
| `class` | The product class or sub-grouping, such as kids, shirts, fragrances, or swimwear. |
| `class_code` | Numeric code identifying the product's class or sub-grouping within a category. |
| `color` | The color of the product. |
| `container` | The container type for the product packaging. |
| `current_price` | The current retail selling price of the product. |
| `formulation` | A product formulation or specification code, likely encoding color or composition details. |
| `manager_code` | Numeric code identifying the manager responsible for this product or product group. |
| `manufacturer` | The name of the company that manufactures the product. |
| `manufacturer_code` | Numeric code identifying the product's manufacturer. |
| `merchandise_code` | Business-facing alphanumeric code identifying a product, shared across historical versions of the same item. |
| `merchandise_description` | A text description of the product, providing additional detail beyond the product name. |
| `merchandise_record` | Internal row identifier for each product record in the merchandise catalog. |
| `product_name` | The display name of the product as it appears in the catalog. |
| `record_effective_end` | The date on which this version of the product record was superseded or expired. |
| `record_effective_start` | The date from which this version of the product record became active. |
| `size` | The size of the product, such as small, medium, large, or N/A for non-sized items. |
| `units` | The unit of measure in which the product is sold, such as Each, Box, Ounce, or Dozen. |
| `wholesale_cost` | The cost paid by the business to acquire or produce the product. |

## online_properties

The online_properties table contains one record per website or digital commerce property operated by the business. It supports online-channel analysis by capturing the site name, opening and closure dates, responsible manager, market grouping, and company affiliation. Address fields (street, city, state, ZIP, country) describe the registered or operational location of each digital property. The table uses slowly changing dimension logic, with record_effective_start and record_effective_end dates tracking historical versions of each property's attributes. A null record_effective_end indicates the currently active version. Tax percentage is stored at the property level to support revenue and tax calculations for online purchases. With 30 rows covering 5 distinct site names, this is a small reference dimension used to group and filter online purchase and refund activity by website, market region, or company.

| Column | Short description |
|---|---|
| `city` | City where the online property's registered address is located. |
| `class` | Classification or tier of the online property. Currently shows 'Unknown' for all records. |
| `closure_calendar_day_ref` | Reference to the calendar day when the online property closed or was decommissioned. Null if still active. |
| `company_code` | Numeric code identifying the company or business entity that owns the online property. |
| `company_name` | Name of the company or business entity that owns the online property. |
| `country` | Country where the online property's address is located. |
| `county` | County where the online property's address is located. |
| `manager` | Name of the manager responsible for the online property or website. |
| `market_class` | Descriptive classification label for the market region associated with the online property. |
| `market_code` | Numeric code identifying the market region the online property belongs to. |
| `market_description` | Longer narrative description of the market region for the online property. |
| `market_manager` | Name of the manager responsible for the market region the online property belongs to. |
| `name` | Display name of the online property or website. |
| `online_property_code` | Business-facing code that identifies a specific online property or website across its historical record versions. |
| `online_property_record` | Internal row identifier for each online property record version. Used as a primary key to join online purchases and refunds. |
| `opening_calendar_day_ref` | Reference to the calendar day when the online property first opened or launched. |
| `record_effective_end` | Date when this version of the online property record expired. Null indicates the currently active version. |
| `record_effective_start` | Date when this version of the online property record became active. |
| `state` | US state where the online property's address is located. |
| `street_name` | Street name of the online property's registered or operational address. |
| `street_number` | Street number of the online property's registered or operational address. |
| `street_type` | Street type suffix for the online property's address, such as Avenue, Boulevard, or Drive. |
| `suite_number` | Suite or unit number within the building at the online property's address. |
| `tax_percentage` | Sales tax rate applied to purchases made through this online property. |
| `timezone_offset` | UTC timezone offset for the online property's location, used for time-of-day analysis. |
| `zip` | Postal ZIP code for the online property's registered address. |

## online_purchases

The online_purchases table records every line-item sale completed through the business's website or digital commerce properties. With nearly 720,000 rows, it captures the full financial picture of each online order: the product sold, the customer who placed and received the order, the website and page where the purchase originated, the marketing campaign associated with the sale, the delivery method chosen, and the fulfillment center that shipped the goods. Financial columns cover unit-level pricing (wholesale cost, list price, sales price) as well as extended amounts scaled by quantity, including discounts, taxes, delivery costs, coupon savings, and multiple net-paid totals. A net profit column allows margin analysis at the transaction level. Dimension references connect each purchase to calendar dates for both sale and shipping timing, client demographic and household profiles for both the billing and shipping parties, and their respective addresses. This table is the primary source for online channel revenue, discount, coupon, delivery cost, and profitability analysis.

| Column | Short description |
|---|---|
| `billing_address_ref` | The billing address associated with this online purchase. |
| `billing_client_profile_ref` | The demographic profile of the billing customer at the time of purchase. |
| `billing_client_ref` | The customer who was billed for this online purchase. |
| `billing_household_profile_ref` | The household profile of the billing customer at the time of purchase. |
| `campaign_ref` | The marketing campaign or promotion associated with this online purchase. |
| `coupon_amount` | The total coupon savings applied to this purchase line. |
| `delivery_method_ref` | The shipping or delivery method selected for this order. |
| `extended_delivery_cost` | Total shipping or delivery cost charged for this purchase line. |
| `extended_discount_amount` | Total discount applied across all units in this purchase line. |
| `extended_list_price` | Total list price value for all units in this purchase line before discounts. |
| `extended_sales_price` | Total revenue from this line item at the actual selling price. |
| `extended_tax` | Total sales tax charged on this purchase line. |
| `extended_wholesale_cost` | Total wholesale cost for all units in this purchase line. |
| `fulfillment_center_ref` | The warehouse that fulfilled and shipped this online order. |
| `list_price` | The standard retail price of the product before any discounts. |
| `merchandise_ref` | The product sold in this online purchase. |
| `net_paid` | Net amount paid by the customer, excluding tax and delivery. |
| `net_paid_with_delivery` | Net amount paid by the customer including delivery costs but excluding tax. |
| `net_paid_with_delivery_tax` | Total amount paid by the customer including both delivery costs and tax. |
| `net_paid_with_tax` | Net amount paid by the customer including sales tax. |
| `net_profit` | Net profit or loss on this online purchase line after costs. |
| `online_property_ref` | The digital commerce website or online property where the purchase was made. |
| `order_number` | The order identifier grouping line items within the same online purchase. |
| `quantity` | The number of units of the product purchased in this line item. |
| `sale_calendar_day_ref` | The calendar date on which the online purchase was made. |
| `sale_clock_time_ref` | The time of day when the online purchase was placed. |
| `sales_price` | The actual per-unit price charged to the customer after discounts. |
| `shipping_address_ref` | The delivery address where the order was shipped. |
| `shipping_calendar_day_ref` | The calendar date on which the order was shipped to the customer. |
| `shipping_client_profile_ref` | The demographic profile of the shipping recipient at the time of purchase. |
| `shipping_client_ref` | The customer to whom the order was shipped. |
| `shipping_household_profile_ref` | The household profile of the shipping recipient at the time of purchase. |
| `site_page_ref` | The website page from which the purchase originated. |
| `wholesale_cost` | The per-unit cost the business paid for the product. |

## online_refunds

The online_refunds table records every item return or refund processed through the website or digital commerce channel. Each row represents a single online return event and captures when the return occurred (date and time), which product was returned, and the two client roles involved: the client who was originally billed (refunded client) and the client who physically submitted the return (returning client). Both client roles are linked to their demographic profiles, household profiles, and addresses at the time of the return, enabling segmentation analysis by geography, income range, and household characteristics. The table also links to the website page associated with the return, the reason the item was returned, and the original order number. Financial columns cover the gross return amount, applicable taxes, total return amount with tax, any processing fees, shipping costs for the return, and the three forms of reimbursement issued: cash refund, reversed charge (credit card reversal), and account credit. The net_loss column summarizes the overall financial loss to the business from the return. This table is the primary source for online return rate analysis, refund method breakdowns, return reason reporting, and customer return behavior studies.

| Column | Short description |
|---|---|
| `account_credit` | The portion of the refund issued as store or account credit. |
| `fee` | A processing or restocking fee charged to the customer for the return. |
| `merchandise_ref` | The product item that was returned. |
| `net_loss` | The net financial loss to the business from the online return. |
| `order_number` | The order number associated with the original online purchase being returned. |
| `refunded_address_ref` | The address of the client receiving the refund. |
| `refunded_cash` | The portion of the refund paid back to the customer as cash. |
| `refunded_client_profile_ref` | The demographic profile of the client being refunded at the time of the return. |
| `refunded_client_ref` | The customer who was originally billed and is receiving the refund. |
| `refunded_household_profile_ref` | The household profile of the client being refunded. |
| `return_amount` | The gross refund amount before tax for the returned items. |
| `return_amount_with_tax` | The total refund amount including tax. |
| `return_calendar_day_ref` | The calendar date on which the online return was processed. |
| `return_clock_time_ref` | The time of day when the online return was processed. |
| `return_delivery_cost` | The shipping or delivery cost associated with the return. |
| `return_quantity` | The number of units returned in this online refund transaction. |
| `return_reason_ref` | The reason the item was returned. |
| `return_tax` | The tax portion of the refund amount. |
| `returning_address_ref` | The address of the client who submitted the return. |
| `returning_client_profile_ref` | The demographic profile of the client who submitted the return. |
| `returning_client_ref` | The customer who physically submitted or initiated the return. |
| `returning_household_profile_ref` | The household profile of the client who submitted the return. |
| `reversed_charge` | The portion of the refund issued as a credit card or charge reversal. |
| `site_page_ref` | The website page associated with the return transaction. |

## retail_locations

This table contains one record per physical retail store (or store version over time), capturing everything needed to analyze the brick-and-mortar sales channel. Each row describes a store's name, size, staffing, operating hours, manager, and full mailing address including city, state, ZIP, county, and country. Market grouping attributes such as market code, market description, and market manager support regional and territory analysis. Division and company fields are present but currently show a single unknown value, suggesting they may not yet be populated. A slowly-changing-dimension pattern is evident: record_effective_start and record_effective_end dates track when each version of a store's attributes was active, and closure_calendar_day_ref records when a store closed. The tax_percentage field supports sales tax calculations by store location. With only 12 rows, this table represents a small chain of physical stores.

| Column | Short description |
|---|---|
| `city` | City where the retail store is located. |
| `closure_calendar_day_ref` | Calendar day reference indicating when the store permanently closed, if applicable. |
| `company_code` | Numeric code identifying the company that owns the retail store. |
| `company_name` | Name of the company that owns or operates the retail store. |
| `country` | Country where the retail store is located. |
| `county` | County where the retail store is located. |
| `division_code` | Numeric code identifying the business division the store belongs to. |
| `division_name` | Name of the business division the store belongs to. |
| `floor_space` | Physical size of the retail store, measured in square feet. |
| `geography_class` | Geographic classification of the store's market area. |
| `hours` | Operating hours of the retail store, expressed as a time range. |
| `manager` | Name of the store manager responsible for the retail location. |
| `market_code` | Numeric code identifying the market or territory the store belongs to. |
| `market_description` | Descriptive text explaining the market or territory associated with the store. |
| `market_manager` | Name of the manager responsible for the store's market or sales territory. |
| `number_employees` | Number of employees working at the retail store. |
| `record_effective_end` | Date when this version of the store's attributes expired or was superseded. |
| `record_effective_start` | Date when this version of the store's attributes became active. |
| `retail_location_code` | Business-facing code that identifies a physical retail store. |
| `retail_location_record` | Internal system identifier uniquely identifying each retail store record row. |
| `state` | US state where the retail store is located. |
| `store_name` | The name of the physical retail store. |
| `street_name` | Street name component of the store's physical address. |
| `street_number` | Street number component of the store's physical address. |
| `street_type` | Street type suffix for the store's address, such as Boulevard, Lane, or Court. |
| `suite_number` | Suite or unit number within the store's building address. |
| `tax_percentage` | Sales tax rate applied to purchases at the retail store. |
| `timezone_offset` | UTC timezone offset for the store's location, used to align transaction timestamps. |
| `zip` | Postal ZIP code for the retail store's address. |

## return_reasons

This small reference table contains 35 distinct return or refund reason codes and their plain-language descriptions. It is used across store, online, and mail-order refund transactions to explain why a shopper returned a product. Example reasons include 'Did not fit', 'Wrong size', 'Package was damaged', 'Found a better price in a store', 'Stopped working', 'Gift exchange', and 'Unauthorized purchase'. Each reason has a unique business-facing code and a human-readable description. This table is joined to refund fact tables to enable analysis of return drivers, product quality issues, pricing competitiveness, and fulfillment problems.

| Column | Short description |
|---|---|
| `reason_description` | Plain-language explanation of why a customer returned or refunded a product. |
| `return_reason_code` | Business-facing code that uniquely identifies a return or refund reason. |
| `return_reason_record` | Internal system identifier uniquely identifying each return reason row. |

## site_pages

This table holds one record per website page (or page version over time), capturing attributes used to analyze the online shopping experience. Each page is described by its type (such as general, welcome, ad, order, feedback, dynamic, or protected), URL, creation date, most recent access date, and content metrics including character count, number of links, number of images, and maximum ad slots. An auto-generated flag distinguishes system-created pages from manually authored ones. A client reference links some pages to specific shoppers, though roughly 65% of pages have no client association, suggesting most pages are public or shared. The slowly-changing-dimension pattern (record_effective_start and record_effective_end) tracks page attribute changes over time. This table supports analysis of website content strategy, page engagement timing, and ad inventory capacity.

| Column | Short description |
|---|---|
| `access_calendar_day_ref` | Calendar day reference indicating the most recent date the website page was accessed. |
| `auto_generated_flag` | Indicates whether the website page was automatically generated by the system. |
| `char_count` | Total number of characters of text content on the website page. |
| `client_ref` | Links the website page to a specific customer account, when applicable. |
| `creation_calendar_day_ref` | Calendar day reference indicating when the website page was created. |
| `image_count` | Number of images displayed on the website page. |
| `link_count` | Number of hyperlinks present on the website page. |
| `max_ad_count` | Maximum number of advertisements that can be displayed on the website page. |
| `record_effective_end` | Date when this version of the website page's attributes expired or was superseded. |
| `record_effective_start` | Date when this version of the website page's attributes became active. |
| `site_page_code` | Business-facing code that identifies a specific website page. |
| `site_page_record` | Internal system identifier uniquely identifying each website page record row. |
| `type` | Category or functional type of the website page, such as general, ad, order, or welcome. |
| `url` | Web address (URL) of the website page. |

## stock_levels

This large fact-style table records the quantity of each merchandise item on hand at each fulfillment center for each calendar day. With nearly 12 million rows, it provides a comprehensive view of inventory availability across the warehouse network. Each row links a specific product (via merchandise_ref), a warehouse location (via fulfillment_center_ref), and a calendar date (via calendar_day_ref) to a quantity_on_hand figure ranging from 0 to 1,000. Approximately 5% of quantity_on_hand values are null, which may indicate missing snapshots or items not tracked on certain days. The table covers all 5 fulfillment centers and all 18,000 merchandise items, making it the primary source for inventory analysis, stockout detection, replenishment planning, and supply-demand comparisons against purchase volumes.

| Column | Short description |
|---|---|
| `calendar_day_ref` | Calendar day reference indicating the date of the inventory snapshot. |
| `fulfillment_center_ref` | Links the stock record to a specific warehouse or fulfillment center. |
| `merchandise_ref` | Links the stock record to a specific product in the merchandise catalog. |
| `quantity_on_hand` | Number of units of a product available in inventory at a fulfillment center on a given day. |

## store_purchases

Records every line-item sale completed at a physical store location. Each row captures the product sold, the shopper and their demographic profile, the store where the purchase occurred, the date and time of the sale, any marketing campaign associated with the transaction, and a full set of financial measures including wholesale cost, list price, actual sales price, discount amount, coupon savings, tax, and net profit. This table is the primary source for in-store revenue analysis, basket-level profitability, campaign effectiveness at retail, and customer purchase behavior segmented by demographics, household profile, or geography.

| Column | Short description |
|---|---|
| `address_ref` | The customer's address associated with this store purchase. |
| `campaign_ref` | The marketing campaign associated with this store purchase. |
| `client_profile_ref` | The demographic profile of the customer at the time of purchase. |
| `client_ref` | The customer who made this in-store purchase. |
| `coupon_amount` | Total coupon savings applied to this purchase line. |
| `extended_discount_amount` | Total discount applied across all units in this purchase line. |
| `extended_list_price` | Total list price for this purchase line (quantity × list price). |
| `extended_sales_price` | Total sales revenue for this purchase line (quantity × sales price). |
| `extended_tax` | Total sales tax charged on this purchase line. |
| `extended_wholesale_cost` | Total wholesale cost for this purchase line (quantity × wholesale cost). |
| `household_profile_ref` | The household profile of the customer at the time of purchase. |
| `list_price` | The standard retail list price for one unit of the product. |
| `merchandise_ref` | The product sold in this store purchase. |
| `net_paid` | Net amount paid by the customer for this purchase line, excluding tax. |
| `net_paid_with_tax` | Net amount paid by the customer including sales tax. |
| `net_profit` | Net profit or loss on this store purchase line. |
| `quantity` | The number of units of the product purchased. |
| `retail_location_ref` | The physical store where this purchase took place. |
| `sale_calendar_day_ref` | The calendar date when the in-store purchase was made. |
| `sale_clock_time_ref` | The time of day when the in-store purchase occurred. |
| `sales_price` | The actual per-unit price charged to the customer. |
| `ticket_number` | The sales receipt or transaction ticket identifier for this store purchase. |
| `wholesale_cost` | The per-unit cost the business paid for the product. |

## store_refunds

Records every item return or refund processed at a physical store location. Each row captures the product returned, the shopper and their demographic profile, the store where the return was processed, the date and time of the return, the reason for the return, and a full set of financial measures including the return amount, tax on the return, any processing fee, delivery cost for the return, cash refunded, charges reversed, store credit issued, and net loss to the business. This table supports analysis of return rates, return reasons, refund methods (cash, charge reversal, store credit), and the financial cost of returns by product, store, customer segment, or time period.

| Column | Short description |
|---|---|
| `address_ref` | The customer's address associated with this store return. |
| `client_profile_ref` | The demographic profile of the returning customer. |
| `client_ref` | The customer who returned the item at the store. |
| `fee` | A processing or restocking fee charged on this return. |
| `household_profile_ref` | The household profile of the returning customer. |
| `merchandise_ref` | The product that was returned in this store refund. |
| `net_loss` | The net financial loss to the business from this store return. |
| `refunded_cash` | The amount refunded to the customer as cash. |
| `retail_location_ref` | The physical store where the return was processed. |
| `return_amount` | The refund amount for this return line, excluding tax. |
| `return_amount_with_tax` | Total refund amount including tax for this return line. |
| `return_calendar_day_ref` | The calendar date when the in-store return was processed. |
| `return_clock_time_ref` | The time of day when the in-store return was processed. |
| `return_delivery_cost` | The shipping or delivery cost associated with this return. |
| `return_quantity` | The number of units returned. |
| `return_reason_ref` | The reason the customer returned the item. |
| `return_tax` | The tax amount refunded on this return. |
| `reversed_charge` | The amount refunded by reversing a credit or charge payment. |
| `store_credit` | The amount refunded to the customer as store credit. |
| `ticket_number` | The sales receipt or transaction identifier associated with this return. |

## support_centers

The support_centers table contains one record per customer service or support center location used by the business to assist shoppers, particularly in mail-order and assisted commerce operations. Each record describes a support center's physical address, size in square feet, number of employees, operating hours, and the manager responsible. Centers are classified by size (e.g., large, medium) and are associated with a market, division, and company for organizational reporting. The table supports slowly changing dimension tracking through record effective start and end dates, allowing historical analysis of center attributes over time. Geographic fields such as city, state, ZIP, county, and country enable location-based analysis. A tax percentage is stored at the center level, and a timezone offset supports time-of-day comparisons across regions. With only 6 rows, this is a small reference dimension. The closure and opening calendar day references link to the calendar_days table to capture when centers opened or closed.

| Column | Short description |
|---|---|
| `city` | City where the support center is located. |
| `class` | Size classification of the support center, such as large or medium. |
| `closure_calendar_day_ref` | Reference to the calendar day when the support center closed. Currently null for all centers, suggesting none have closed. |
| `company` | Numeric code identifying the company or legal entity the support center belongs to. |
| `company_name` | Name of the company or corporate entity the support center belongs to. |
| `country` | Country where the support center is located. |
| `county` | County where the support center is located. |
| `division` | Numeric code identifying the organizational division the support center belongs to. |
| `division_name` | Name of the organizational division the support center belongs to. |
| `employees` | Number of employees working at the support center. |
| `hours` | Operating hours of the support center, such as 8AM–4PM. |
| `manager` | Name of the manager responsible for the support center. |
| `market_class` | Descriptive classification of the support center's market area. |
| `market_code` | Numeric code identifying the market area associated with the support center. |
| `market_description` | Longer description of the market area served by the support center. |
| `market_manager` | Name of the manager responsible for the support center's market area. |
| `name` | The name of the support center, often reflecting its regional market area. |
| `opening_calendar_day_ref` | Reference to the calendar day when the support center opened for business. |
| `record_effective_end` | Date when this version of the support center record expired. Null indicates the record is currently active. |
| `record_effective_start` | Date when this version of the support center record became active. |
| `square_ft` | Physical size of the support center in square feet. |
| `state` | US state where the support center is located. |
| `street_name` | Street name component of the support center's physical address. |
| `street_number` | Street number component of the support center's physical address. |
| `street_type` | Street type suffix for the support center's address, such as Boulevard or Court. |
| `suite_number` | Suite or unit number within the support center's building address. |
| `support_center_code` | Business-facing code that identifies a support center, used to link support centers to purchase and refund records. |
| `support_center_record` | Internal row identifier for each support center record. Appears to be a system-generated surrogate key. |
| `tax_percentage` | Local tax rate applicable at the support center's location, expressed as a decimal. |
| `timezone_offset` | UTC timezone offset for the support center's location, used for time-of-day analysis. |
| `zip` | Postal ZIP code for the support center's location. |
