![](https://docs.appfigures.com/wp-content/uploads/2018/11/appfigures-logo-white.png)[Login](https://appfigures.com/login)

The `/products` resource provides access to product meta data in a variety of ways.

Contents

- [What is a product](https://docs.appfigures.com/api/reference/v2/products-2#what-is-a-product)
- [Getting a product by its Appfigures ID](https://docs.appfigures.com/api/reference/v2/products-2#getting-a-product-by-its-appfigures-id)
- [Searching for products](https://docs.appfigures.com/api/reference/v2/products-2#searching-for-products)
- [Getting a product by its ID in the store](https://docs.appfigures.com/api/reference/v2/products-2#getting-a-product-by-its-id-in-the-store)
- [Listing all of your products](https://docs.appfigures.com/api/reference/v2/products-2#listing-all-of-your-products)
- [Updating products](https://docs.appfigures.com/api/reference/v2/products-2#updating-products)
- [Getting Extra Product Metadata](https://docs.appfigures.com/api/reference/v2/products-2#getting-extra-product-metadata)

## What is a product

Products represent a trackable item in the Appfigures system. Most commonly a product is an app, but since we support more than just apps a product may be an in-app purchase (IAP) or a book.

We collect product data directly from the store the product is sold in. The following properties are available for all products, including products not owned by or shared with your account.

| Field | Type | Description |
| --- | --- | --- |
| id | Number | The product’s Appfigures-assigned id. |
| name | string | Full name of the product. |
| developer | string | Name of the developer of the product. |
| icon | string | URL to the product’s icon on the store. |
| vendor\_identifier | string | The ref\_no or sku of the product as used by the store to uniquely identify this product. |
| ref\_no | int | The product’s store id if one is available (only applies to Apple apps currently). |
| sku | string | The developer-assigned SKU or package name. |
| store\_id | int | Id of store this product is sold on. |
| store | string | Name of store this product is sold on. Options: “apple”, “google\_play”, etc. A full list of stores is available at /data/stores |
| release\_date | date | The date the product was released to the store. |
| added\_date | date | Date product was added by Appfigures. |
| updated\_date | date | Date the product was last updated by Appfigures. |
| version | string | The product’s current version. |
| type | string | Type of product. Options: “app”, “inapp”, “book”. |
| devices | array | A list of devices the product is sold on. Options: “Handheld” (Ex. iPhone), “Tablet” (Ex. iPad), “Desktop”. |
| active | bool | Whether the product is active in the store. |

Products that are owned by or shared with your account will include additional properties specific to your account:

| Field | Type | Description |
| --- | --- | --- |
| parent\_id | int | If this is an in-app purchase, this is the product id of the parent product. |
| children | array | If this is product has in-app purchases, a list of product ids of all the in-app purchases belonging to this product. |
| features | array | A list of all additional features the product supports. Options: iads, admob. |
| source | object | Information describing the product within your Appfigures account:<br>**external\_account\_id**<br>the external account this app came through.<br>**added\_timestamp**<br>Date the product was added to your account.<br>**active**<br>Whether the product is actively being tracked in your Appfigures account.<br>**hidden**<br>Whether the product is visible in the current user’s product list on the site.<br>**type**<br>Whether the app is owned by or shared with your account. Options: own, shared. |

## Getting a product by its Appfigures ID

```
GET /products/{id}
```

| Field | Type | Description |
| --- | --- | --- |
| id | Number | The Appfigures-assigned id for the product you want to get |

### Request

```
GET /products/212135374
```

### Example Response

```
// GET /products/212135374
{
  "id": 212135374,
  "name": "Rdio",
  "developer": "Rdio",
  "icon": "http://ecx.images-amazon.com/images/I/51IPlKhP19L._SL160_SL150_.png",
  "vendor_identifier": "B004T76OR8",
  "package_name": "B004T76OR8",
  "store_id": 3,
  "store": "amazon_appstore",
  "release_date": "2000-01-01T00:00:00",
  "added_date": "2012-05-30T19:16:43",
  "updated_date": "2013-07-02T21:00:00",
  "version": "2.6.3",
  "source": null,
  "type": "unknown",
  "devices": [\
    "Handheld"\
  ],
  "price": {
    "currency": "USD",
    "price": "0.00"
  }
}
```

## Searching for products

> The [Public Data API](https://docs.appfigures.com/public-data-access) add-on is required for this route.

> **Scope:** This resource requires the **public:read** scope.

### Request

```
GET /products/search/{term}?filter={filter}&page={page}&count={count}
```

### Arguments

. This parameter is deprecated, you should use the storefronts filter instead.

| Field | Type | Description |
| --- | --- | --- |
| term | String | The name of a product or a developer. Prefix with @name= or @developer= if you’d like to search that field only. |
| filter (deprecated) | String | Filter to apply to the results: one of ios, mac, google, amazon. Defaults to showing all results |
| storefronts | String | Semicolon-separated set of storefronts to filter the results by. \`apple\`, \`apple:mac\`, \`google\_play\`, \`amazon\_appstore\`, and \`windows10\` are some valid examples. All available storefronts can be found by using the [/data/stores](https://docs.appfigures.com/api/reference/v2/data#Listing_Full_store_information) route. |
| page | Number | Page of results to show. Defaults to the first page |
| count | Number | Number of results to show in a page. Defaults to 25 |

### Example

```
// GET /products/search/fob+finder
[\
  {\
    "id": 6528381,\
    "name": "FOB Finder",\
    "developer": "Soul Doubt Productions",\
    "icon": "http://a715.phobos.apple.com/us/r1000/028/Purple/26/31/e1/mzi.hldsckxh.png",\
    "vendor_identifier": "420200383",\
    "ref_no": 420200383,\
    "sku": "",\
    "store_id": 1,\
    "store": "apple",\
    "release_date": "2011-01-22T00:02:00",\
    "added_date": "2011-11-22T23:53:45",\
    "updated_date": "2011-11-02T13:00:02",\
    "version": "1.76",\
    "source": null,\
    "type": "app",\
    "devices": [\
      "Handheld",\
      "Tablet"\
    ],\
    "price": {\
      "currency": "USD",\
      "price": "0.00"\
    }\
  }\
]
```

```
// GET /products/search/@developer=angry
[\
  {\
    "id": 212138192,\
    "name": "GPS Notification",\
    "developer": "Angry Face Software",\
    "icon": "http://ecx.images-amazon.com/images/I/51amiNe%2BwFL._SL190_CR0,0,190,246_.png",\
    "vendor_identifier": "B004QP96SM",\
    "ref_no": null,\
    "sku": "B004QP96SM",\
    "package_name": "B004QP96SM",\
    "store_id": 3,\
    "store": "amazon_appstore",\
    "release_date": "2000-01-01T00:00:00",\
    "added_date": "2012-05-30T19:16:49",\
    "updated_date": "2013-11-08T02:56:19",\
    "version": "1.3",\
    "source": null,\
    "type": "unknown",\
    "devices": [\
      "Handheld"\
    ],\
    "price": {\
      "currency": "USD",\
      "price": "0.99"\
    }\
  },\
  . . .\
]
```

## Getting a product by its ID in the store

```
GET /products/{store}/{id_in_store}
```

store

The store that the product is from. apple, google\_play, amazon\_appstore are some valid examples. All available stores can be found by using the [/data/stores](https://docs.appfigures.com/api/reference/v2/data#Listing_Full_store_information) route.

id\_in\_store

The product’s unique identifier in the store (Apple ID for iOS/Mac apps, SKU for Google Play, and ASIN for Amazon Appstore apps)

> **Important:** When you request a product that doesn’t exist in our database we will attempt to look up the product in the store in real time. This happens automatically for all stores.
>
> Depending on the store however, the lookup may take some time. The API will wait up to 90 seconds for such a lookup. If we were able to find the product and collect relevant information, it will be returned.
>
> If the lookup process is still running after 90 seconds however, the lookup will continue asynchronously and you will receive a 202 response with a url to look up the status.

### Requests

```
GET /products/apple/335060889
GET /products/google_play/com.rdio.android.ui
GET /products/amazon/B004T76OR8
```

### Example Responses

```
// GET /products/apple/335060889
{
  "id": 5985606,
  "name": "Rdio",
  "developer": "Rdio",
  "icon": "http://a1857.phobos.apple.com/us/r1000/081/Purple/v4/f5/6b/0d/f56b0d3b-5a5c-013d-bc08-1c7cc9aed3a3/mzl.mlvhudjt.75x75-65.png",
  "vendor_identifier": "335060889",
  "ref_no": 335060889,
  "sku": "",
  "store_id": 1,
  "store": "apple",
  "release_date": "2009-12-17T00:00:00",
  "added_date": "2011-11-29T16:04:22",
  "updated_date": "2013-05-22T14:00:00",
  "version": "2.2.3",
  "source": null,
  "type": "unknown",
  "devices": [\
    "Handheld",\
    "Tablet"\
  ],
  "price": {
    "currency": "USD",
    "price": "0.00"
  }
}
```

```
// GET /products/google_play/com.rdio.android.ui
{
  "id": 5555936,
  "name": "Rdio",
  "developer": "Rdio",
  "icon": "https://lh3.ggpht.com/L3w6B50SgmFOt8q_poF25XArA6EOJJSf7SOjBe9kPftnPVq8uS9JgtmJAwLVp_pN7ig",
  "vendor_identifier": "com.rdio.android.ui",
  "package_name": "com.rdio.android.ui",
  "store_id": 2,
  "store": "google_play",
  "release_date": "2000-01-02T00:00:00",
  "added_date": "2011-12-02T23:21:16",
  "updated_date": "2013-06-04T19:00:00",
  "version": "Varies with device",
  "source": null,
  "type": "unknown",
  "devices": [\
    "Handheld"\
  ],
  "price": {
    "currency": "USD",
    "price": "0.00"
  }
}
```

```
// GET /products/amazon/B004T76OR8
{
  "id": 212135374,
  "name": "Rdio",
  "developer": "Rdio",
  "icon": "http://ecx.images-amazon.com/images/I/51IPlKhP19L._SL160_SL150_.png",
  "vendor_identifier": "B004T76OR8",
  "package_name": "B004T76OR8",
  "store_id": 3,
  "store": "amazon_appstore",
  "release_date": "2000-01-01T00:00:00",
  "added_date": "2012-05-30T19:16:43",
  "updated_date": "2013-07-02T21:00:00",
  "version": "2.6.3",
  "source": null,
  "type": "unknown",
  "devices": [\
    "Handheld"\
  ],
  "price": {
    "currency": "USD",
    "price": "0.00"
  }
}
```

## Listing all of your products

```
GET /products/mine/?store={store}&include_inactive={true|false}
```

> **Scope:** This resource requires the **products:read** scope.

| Field | Type | Description |
| --- | --- | --- |
| store | String | Filter to only show products from a given store. apple, google\_play, amazon\_appstore are some valid examples. All available stores can be found by using the [/data/stores](https://docs.appfigures.com/api/reference/v2/data#Listing_Full_store_information) route. |
| include\_inactive | Boolean | Whether to include inactive products in the response. Defaults to false, meaning inactive products are not returned. |

### Request

```
GET /products/mine?store=apple
```

### Example Response

```
// GET /products/212135374?store=apple
{
  "42": {
    "id": 42,
    "name": "Fob Finder Pro",
    "developer": "Consolidated Fob",
    "icon": "http://a4.mzstatic.com/us/r1000/030/Purple/75/f4/1a/mzl.fjpeofny.100x100-75.jpg",
    "vendor_identifier": "300101010",
    "ref_no": 300101010,
    "sku": null,
    "store_id": 1,
    "store": "apple",
    "release_date": "2008-11-15T05:00:00",
    "added_date": "2011-11-28T19:06:11",
    "updated_date": "2013-06-26T09:02:44",
    "version": "1.2",
    "source": {
      "external_account_id": 32,
      "added_timestamp": "2009-01-02T17:50:00",
      "active": true,
      "hidden": false,
      "type": "own"
    },
    "type": "unknown",
    "devices": [\
      "Handheld",\
      "Tablet"\
    ],
    "children": [],
    "features": [],
    "parent_id": null,
    "price": {
      "currency": "USD",
      "price": "1.00"
    }
  },
   . . .
}
```

## Updating products

Right now we only update you can make is changing the values of `source.active` and `source.hidden` to change how the product shows up in your account.

It is an error to make this call on a product you don’t own.

### Request

```
PUT /products/{product_id}
```

> **Scope:** This resource requires the **products:write** scope.

### Arguments (as json in request body)

| Field | Type | Description |
| --- | --- | --- |
| source | String | Information about the origin of this product<br>- **active** – Whether or not the product will import<br>- **hidden** – Whether or not this product will show up in the UI |

### Example Request

```
// GET /products/31175

{
    id: 31175,
    name: "Plane Finder",
     . . .
    version: " 2.0.1 ",
    source: {
        external_account_id: 23830,
        added_timestamp: "2012-02-27T19:09:36",
        active: true,
        hidden: false,
        type: "own"
    },
     . . .
}
```

```
// PUT /external_accounts/31175

{
    id: 31175,
    source: {
        active: false,
        hidden: true,
    }
}
```

### Example Response

```
// PUT /products/31175  with above request body

{
    id: 31175,
    name: "Plane Finder",
     . . .
    version: " 2.0.1 ",
    source: {
        external_account_id: 23830,
        added_timestamp: "2012-02-27T19:09:36",
        active: false,
        hidden: true,
        type: "own"
    },
     . . .
}
```

## Getting Extra Product Metadata

> The [Public Data API](https://docs.appfigures.com/public-data-access) add-on is required for this route.

> Adding _meta=true_ will require five additional credits.

This isn’t a route so much as a setting that can be specified.

Most places you can be given products you can also ask for additional data from the products by appending the `meta=true` to your request. Passing this parameter adds two more entries to the product representation `meta` and `categories`.

### Meta

The `meta` property is a collection of attributes grouped by language. The following attributes are currently available. More or different attributes may become available in the future.

- description
- developer\_email
- developer\_site
- downloads
- name
- rating
- release\_notes
- view\_url
- download\_size
- company\_url
- support\_url
- artwork\_url\_large
- artwork\_url\_small
- bundle\_identifier
- copyright
- itunes\_version
- primary\_description
- recommended\_age
- seller\_name

Not all attributes are available for all products or stores. For instance, `itunes_version` is obviously Apple only, and `downloads` is Google Play only.

The full representation of the `meta` property will look like this:

```
 "meta": {
      "en": [\
        {\
          "key": "artwork_url_large",\
          "language": "en",\
          "value": "http://a3.mzstatic.com/us/r1000/010/Purple/31/a8/12/mzi.ixsiihjs.100x100-75.jpg"\
        },\
        {\
          "key": "artwork_url_small",\
          "language": "en",\
          "value": "http://a2.mzstatic.com/us/r1000/012/Purple/05/1a/c0/mzi.hgvovgne.png"\
        },\
        {\
          "key": "company_url",\
          "language": "en",\
          "value": "http://www.flyloopgame.com"\
        },\
        {\
          "key": "copyright",\
          "language": "en",\
          "value": "© 2010 Spiralstorm Games Inc."\
        },\
        {\
          "key": "description",\
          "language": "en",\
          "value": "********************************************Watch the video at www.flyloopgame.com********************************************New: Now supp *snip*"\
        }\
      ],
      "fr":{
         . . .
      }
      . . .
  }
```

### Categories

The `categories` property is an array containing the categories the app is available for sale in currently or in has been in the past. The representation looks exactly like the representation described in [Listing app store categories](https://docs.appfigures.com/data/categories), with the addition of a property for showing whether or not this is the main category for the app. At this time only iOS and Mac apps support the `main` property.

The representation for categories looks like this:

```
    "categories": [\
      {\
        "store": "apple",\
        "store_id": 1,\
        "device": "handheld",\
        "device_id": 1,\
        "name": "Games",\
        "id": 6014,\
        "parent_id": null,\
        "parent": null,\
        "main": true\
      },\
      . . .\
    ]
```

### Example Response

```
// GET /products/search/embark?meta=true
[\
  {\
    "id": 6725579,\
    "name": "Embark NYC Subway – New York City",\
    "developer": "Embark, Inc.",\
    "icon": "http://a1.mzstatic.com/us/r1000/101/Purple2/v4/75/17/1f/75171fa5-6f04-dca8-a439-f8b01bbec3e5/mzl.xxcfhuch.100x100-75.jpg",\
    "vendor_identifier": "450991137",\
    "ref_no": 450991137,\
    "sku": "",\
    "store_id": 1,\
    "store": "apple",\
    "release_date": "2011-07-31T00:00:00",\
    "added_date": "2011-12-18T16:03:40",\
    "updated_date": "2013-08-07T09:04:29",\
    "version": "1.5.3",\
    "source": null,\
    "type": "unknown",\
    "devices": [\
      "Handheld",\
      "Tablet"\
    ],\
    "price": {\
      "currency": "USD",\
      "price": "0.00"\
    },\
    "meta": {\
      "en": [\
        {\
          "key": "description",\
          "language": "en",\
          "value": "Embark NYC Subway makes it easy to get around New York. Embark even works without cellular signal! Features: – Offline trip planning between subway stations – Interactive MTA Subway map – *snip*"\
        },\
        {\
          "key": "view_url",\
          "language": "en",\
          "value": "http://itunes.apple.com/app/embark-nyc-subway-new-york/id450991137?uo=5"\
        }\
         . . .\
      ]\
    },\
    "categories": [\
      {\
        "store": "apple",\
        "store_id": 1,\
        "device": "handheld",\
        "device_id": 1,\
        "name": "Travel",\
        "id": 6003,\
        "parent_id": null,\
        "parent": null,\
        "main": false\
      },\
      . . .\
    ]\
  }\
```